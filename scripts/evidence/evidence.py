import argparse
import json
import re
import urllib.request
from collections import Counter
from pathlib import Path

PAPERS_PATH = Path(__file__).with_name("papers.json")
BUCKET_URL = "https://pmc-oa-opendata.s3.amazonaws.com/"
SUMMARY_PREFIXES = ["대상:", "방법:", "효과:"]
MAX_LINE_LENGTH = 60
NON_COMMERCIAL = re.compile(r"BY-NC|BY-ND|NonCommercial|NoDerivatives", re.IGNORECASE)
SITUATIONS_PER_CHUNK = range(2, 4)


def fetch(out: Path) -> None:
    """1단계 CC BY 논문 본문 다운로드"""
    (out / "text").mkdir(parents=True, exist_ok=True)
    index = []
    for paper in json.loads(PAPERS_PATH.read_text()):
        meta = _pmc_metadata(paper["pmcid"])
        if meta.get("license_code") != "CC BY":
            print(f"SKIP {paper['name']}: 라이선스 {meta.get('license_code')}")
            continue
        paper_text = _download(_https(meta["text_url"])).decode()
        if NON_COMMERCIAL.search(_license_line(paper_text)):
            print(f"SKIP {paper['name']}: 본문 라이선스 {_license_line(paper_text)}")
            continue
        (out / "text" / f"{paper['name']}.txt").write_text(paper_text)
        index.append({
            "name": paper["name"],
            "citation": meta["citation"],
            "doi_url": f"https://doi.org/{meta['doi']}",
            "license": meta["license_code"],
        })
        print(f"OK   {paper['name']}")
    (out / "papers.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))


def build(out: Path) -> None:
    """5단계 명제, 청크, 상황 검증 후 chunks.json 조립"""
    chunks = []
    errors = []
    for paper in json.loads((out / "papers.json").read_text()):
        name = paper["name"]
        paper_text = _normalize((out / "text" / f"{name}.txt").read_text())
        propositions = {p["id"]: p for p in json.loads((out / "propositions" / f"{name}.json").read_text())}
        paper_chunks = json.loads((out / "chunks" / f"{name}.json").read_text())
        situations = json.loads((out / "situations" / f"{name}.json").read_text())
        errors += [f"{name} {error}" for error in _check_propositions(propositions, paper_text)]
        errors += [f"{name} {error}" for error in _check_assignment(propositions, paper_chunks)]
        errors += [f"{chunk['id']}: {error}" for chunk in paper_chunks for error in _check_summary(chunk)]
        errors += [f"{name} {error}" for error in _check_situations(situations, paper_chunks)]
        chunks += [_assemble(chunk, propositions, situations, paper) for chunk in paper_chunks]
    ids = Counter(chunk["id"] for chunk in chunks)
    errors += [f"{chunk_id}: 중복 id" for chunk_id, count in ids.items() if count > 1]
    if errors:
        raise SystemExit("\n".join(errors))
    (out / "chunks.json").write_text(json.dumps(chunks, ensure_ascii=False, indent=1))
    print(f"{len(chunks)}개 청크 OK")


def _check_propositions(propositions: dict, paper_text: str) -> list[str]:
    """명제 인용이 원문에 그대로 있는지 검사"""
    return [f"{pid}: quote 가 원문에 없음" for pid, p in propositions.items() if _normalize(p["quote"]) not in paper_text]


def _check_assignment(propositions: dict, chunks: list[dict]) -> list[str]:
    """모든 명제가 정확히 한 청크에 배정됐는지 검사"""
    assigned = Counter(pid for chunk in chunks for pid in chunk["propositions"])
    errors = [f"{pid}: 없는 명제" for pid in assigned if pid not in propositions]
    errors += [f"{pid}: {count}개 청크에 중복 배정" for pid, count in assigned.items() if count > 1]
    errors += [f"{pid}: 배정 안 됨" for pid in propositions if pid not in assigned]
    return errors


def _check_summary(chunk: dict) -> list[str]:
    """요약이 대상, 방법, 효과 3줄 형식인지 검사"""
    errors = []
    if [line[:3] for line in chunk["summary"]] != SUMMARY_PREFIXES:
        errors.append("summary 가 대상, 방법, 효과 3줄이 아님")
    errors += [f"{len(line)}자 초과: {line}" for line in chunk["summary"] if len(line) > MAX_LINE_LENGTH]
    return errors


def _check_situations(situations: dict, chunks: list[dict]) -> list[str]:
    """청크마다 상황 문장이 2~3개인지 검사"""
    chunk_ids = {chunk["id"] for chunk in chunks}
    errors = [f"{chunk_id}: 없는 청크의 상황" for chunk_id in situations if chunk_id not in chunk_ids]
    counts = {chunk_id: len(situations.get(chunk_id, [])) for chunk_id in chunk_ids}
    errors += [f"{chunk_id}: 상황 {count}개" for chunk_id, count in counts.items() if count not in SITUATIONS_PER_CHUNK]
    return errors


def _assemble(chunk: dict, propositions: dict, situations: dict, paper: dict) -> dict:
    """청크에 명제 본문, 원문 인용, 상황, 출처 결합"""
    members = [propositions[pid] for pid in chunk["propositions"] if pid in propositions]
    return {
        "id": chunk["id"],
        "paper": paper["name"],
        "title": chunk["title"],
        "summary": chunk["summary"],
        "propositions": [p["text"] for p in members],
        "source_text": " ".join(p["quote"] for p in members),
        "situations": situations.get(chunk["id"], []),
        "citation": paper["citation"],
        "doi_url": paper["doi_url"],
        "license": paper["license"],
    }


def _license_line(paper_text: str) -> str:
    """본문 License 줄, 메타데이터와 어긋나는 논문 거르기용"""
    match = re.search(r"^License:.*$", paper_text, re.MULTILINE)
    return match.group(0) if match else ""


def _pmc_metadata(pmcid: str) -> dict:
    """PMC 오픈데이터 최신 버전 메타데이터"""
    listing = _download(f"{BUCKET_URL}?list-type=2&prefix=metadata/{pmcid}.").decode()
    keys = re.findall(r"<Key>([^<]+)</Key>", listing)
    if not keys:
        return {}
    latest = max(keys, key=lambda key: int(key.split(".")[-2]))
    return json.loads(_download(BUCKET_URL + latest))


def _https(s3_url: str) -> str:
    """s3 경로를 공개 HTTPS 주소로"""
    return s3_url.split("?")[0].replace("s3://pmc-oa-opendata/", BUCKET_URL)


def _download(url: str) -> bytes:
    """URL 본문 다운로드"""
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def _normalize(text: str) -> str:
    """특수 공백 포함 연속 공백 한 칸으로"""
    return re.sub(r"\s+", " ", text).strip()


def main() -> None:
    """fetch 또는 build 단계 실행"""
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=["fetch", "build"])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    {"fetch": fetch, "build": build}[args.step](args.out)


if __name__ == "__main__":
    main()
