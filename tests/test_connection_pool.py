"""커넥션 풀 재사용 실측 — AsyncClient를 요청마다 만들면 keep-alive가 왜 무의미해지는가.

로컬 keep-alive HTTP 서버가 "새 TCP 커넥션 수"를 세고, 5개 동시 × 10배치를 보낸다.
실제 트래픽(배치 간 5초)을 시간 축만 압축했다 — 간격 0.15초에 만료를 그보다
짧게/길게 줘서 같은 물리를 재현한다:

  A) 요청마다 새 AsyncClient           → 요청 수만큼 커넥션 (재사용 0)
  B) 공유 + 만료가 간격보다 짧음        → 배치마다 만료돼 다시 뚫음 (A와 같은 커넥션 수)
  C) 공유 + 만료가 간격보다 긺          → 동시성만큼만 뚫고 끝까지 재사용

pytest -s tests/test_connection_pool.py 로 실행하면 수치가 print로 보인다.
운영 대응: lru_cache 공유(clients/spring.py) = C. lru_cache가 없으면 A가 된다.
"""
import asyncio
import statistics
import time

import httpx
import pytest

BATCHES, BATCH_SIZE, GAP = 10, 5, 0.15
TOTAL = BATCHES * BATCH_SIZE


class _CountingServer:
    """커넥션 1개당 1 증가 — keep-alive 재사용된 후속 요청은 안 늘어난다."""

    def __init__(self):
        self.connections = 0
        self._server: asyncio.Server | None = None

    async def __aenter__(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"
        return self

    async def __aexit__(self, *_):
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        # 본문은 팀 봉투 JSON — 운영 SpringInternalClient(_get이 data를 벗김)도 그대로 통과한다
        body = b'{"traceId":"t","data":{}}'
        head = (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\nConnection: keep-alive\r\n\r\n"
        )
        try:
            while True:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(head + body)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            return
        finally:
            writer.close()


async def _drive(request_once) -> list[float]:
    """5개 동시 × 10배치, 배치 간 GAP 초 — 요청별 지연 목록을 돌려준다."""
    latencies: list[float] = []

    async def timed() -> float:
        t0 = time.perf_counter()
        await request_once()
        return time.perf_counter() - t0

    for batch in range(BATCHES):
        latencies.extend(await asyncio.gather(*[timed() for _ in range(BATCH_SIZE)]))
        if batch < BATCHES - 1:
            await asyncio.sleep(GAP)
    return latencies


def _report(name: str, server: _CountingServer, latencies: list[float]) -> None:
    print(
        f"\n{name}: 새 TCP {server.connections}개/{TOTAL}요청, "
        f"지연 평균 {statistics.mean(latencies)*1000:.2f}ms, 최대 {max(latencies)*1000:.2f}ms"
    )


@pytest.mark.asyncio
async def test_요청마다_새_클라이언트면_커넥션_재사용_0():
    async with _CountingServer() as server:

        async def per_request():
            async with httpx.AsyncClient(base_url=server.base_url) as client:
                await client.get("/")

        latencies = await _drive(per_request)
        _report("A) 요청마다 새 AsyncClient", server, latencies)
        assert server.connections == TOTAL   # 100% 새로 뚫음 — lru_cache 없는 상황


@pytest.mark.asyncio
async def test_공유해도_만료가_트래픽_간격보다_짧으면_매번_다시_뚫는다():
    async with _CountingServer() as server:
        client = httpx.AsyncClient(
            base_url=server.base_url,
            limits=httpx.Limits(keepalive_expiry=GAP / 3),   # 만료 < 배치 간격
        )
        latencies = await _drive(lambda: client.get("/"))
        _report("B) 공유 + 만료 < 간격", server, latencies)
        await client.aclose()
        # 배치 안에서는 재사용돼도 배치 사이마다 만료 — 첫 배치 이후 매 배치 다시 뚫는다
        assert server.connections >= BATCH_SIZE * (BATCHES - 1)


@pytest.mark.asyncio
async def test_공유하고_만료가_간격보다_길면_동시성만큼만_뚫는다():
    async with _CountingServer() as server:
        client = httpx.AsyncClient(
            base_url=server.base_url,
            limits=httpx.Limits(keepalive_expiry=30.0),      # 만료 > 배치 간격
        )
        latencies = await _drive(lambda: client.get("/"))
        _report("C) 공유 + 만료 > 간격 (운영 lru_cache 구조)", server, latencies)
        await client.aclose()
        # 이상적으론 동시성(5)개 — 반납 타이밍 경합으로 한두 개 더 뚫릴 수 있어 여유를 둔다
        assert server.connections <= BATCH_SIZE + 3
        assert server.connections < TOTAL // 5   # 요청 수 대비 압도적으로 적음이 핵심


def test_운영_스프링_클라이언트는_lru_cache_싱글턴():
    """배선 검증 — get_spring_client가 항상 같은 인스턴스를 돌려줘야 위 C 시나리오가 성립한다."""
    from app.clients.spring import get_spring_client

    assert get_spring_client() is get_spring_client()


@pytest.mark.asyncio
async def test_운영_팩토리를_매_요청_불러도_C와_같은_수치가_나온다(monkeypatch):
    """D) 진짜 get_spring_client()로 같은 부하를 보낸다 — 운영 코드의 실제 사용 형태.

    서비스 코드는 요청마다 get_spring_client().get_...을 부른다. lru_cache 덕에
    이 형태가 A(매번 새 클라이언트)가 아니라 C(공유)와 같은 수치임을 실측한다.
    """
    from app.clients import spring
    from app.core.config import get_settings

    async with _CountingServer() as server:
        # 운영 base_url(스프링 내부 주소)을 카운팅 서버로 돌리고, 캐시된 구 클라이언트 폐기
        monkeypatch.setattr(get_settings(), "spring_internal_base_url", server.base_url)
        spring.get_spring_client.cache_clear()
        try:
            async def via_factory():
                # 요청마다 팩토리 호출 — 서비스 코드와 동일한 사용 형태
                await spring.get_spring_client().get_exercise("barbell-squat")

            latencies = await _drive(via_factory)
            _report("D) 운영 get_spring_client() 매 요청 호출", server, latencies)
            assert server.connections <= BATCH_SIZE + 3     # C와 같은 기준
            assert server.connections < TOTAL // 5
        finally:
            # 카운팅 서버를 가리키는 클라이언트가 다른 테스트로 새지 않게 닫고 캐시를 비운다
            await spring.get_spring_client()._client.aclose()
            spring.get_spring_client.cache_clear()
