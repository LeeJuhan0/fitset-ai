from enum import StrEnum


class Level(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class Goal(StrEnum):
    STRENGTH = "strength"
    HYPERTROPHY = "hypertrophy"
    WEIGHT_LOSS = "weightLoss"
    ENDURANCE = "endurance"


class MuscleGroup(StrEnum):
    BACK = "back"
    BICEPS = "biceps"
    CALVES = "calves"
    CHEST = "chest"
    CORE = "core"
    FOREARMS = "forearms"
    GLUTES = "glutes"
    HAMSTRINGS = "hamstrings"
    QUADRICEPS = "quadriceps"
    SHOULDERS = "shoulders"
    TRAPEZIUS = "trapezius"
    TRICEPS = "triceps"
