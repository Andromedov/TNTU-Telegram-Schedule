import re
from html import escape
from typing import Iterable

from messages import get_msg, normalize_language


CAMPUS_MAP_URL = "https://www.google.com/maps/d/viewer?mid=1CYr1ELkD1Pv6nbimq3kz_uv6a1ClLJpK"

BUILDINGS = {
    1: {
        "uk": ("вул. Руська, 56", "8-поверхова будівля сірого кольору, одразу біля мосту."),
        "en": ("56 Ruska Street", "An eight-storey grey building immediately beside the bridge."),
    },
    2: {
        "uk": ("вул. Руська, 56", "3-поверхова будівля біля мосту."),
        "en": ("56 Ruska Street", "A three-storey building beside the bridge."),
    },
    3: {
        "uk": ("вул. Федьковича, 9", "Спустіться вниз від корпусу 1 і поверніть ліворуч."),
        "en": ("9 Fedkovycha Street", "Walk downhill from Building 1 and turn left."),
    },
    4: {
        "uk": ("вул. Руська, 56А", "У дворі, позаду корпусу 2."),
        "en": ("56A Ruska Street", "In the courtyard behind Building 2."),
    },
    5: {
        "uk": ("вул. Старий Поділ, 2", "Навпроти площі Героїв Євромайдану."),
        "en": ("2 Staryi Podil Street", "Opposite Heroes of Euromaidan Square."),
    },
    6: {
        "uk": ("вул. Гоголя, 6", "Позаду корпусу 8."),
        "en": ("6 Hoholia Street", "Behind Building 8."),
    },
    7: {
        "uk": ("вул. Микулинецька, 46", "Одразу біля дороги."),
        "en": ("46 Mykulynetska Street", "Immediately beside the road."),
    },
    8: {
        "uk": ("вул. Гоголя, 8", "Навпроти міської дитячої поліклініки."),
        "en": ("8 Hoholia Street", "Opposite the municipal children's clinic."),
    },
    9: {
        "uk": ("вул. Текстильна, 28", "Неподалік від ТРЦ «Подоляни»."),
        "en": ("28 Tekstylna Street", "Near the Podoliany shopping centre."),
    },
    10: {
        "uk": ("вул. Білогірська, 50", "Від приміського автовокзалу йдіть вулицею Білогірською."),
        "en": ("50 Bilohirska Street", "From the suburban bus station, walk along Bilohirska Street."),
    },
    11: {
        "uk": ("вул. Лук'яновича, 8", "Неподалік від ТЦ «Епіцентр»."),
        "en": ("8 Lukianovycha Street", "Near the Epicentr shopping centre."),
    },
}


def building_number(value: str | int | None) -> int | None:
    if isinstance(value, int):
        return value if value in BUILDINGS else None
    match = re.fullmatch(r"[КK]\s*(\d{1,2})", str(value or "").strip(), re.IGNORECASE)
    number = int(match.group(1)) if match else None
    return number if number in BUILDINGS else None


def schedule_buildings(schedules: Iterable[Iterable[dict]]) -> list[int]:
    result = {
        number
        for schedule in schedules
        for lesson in schedule
        if (number := building_number(lesson.get("building"))) is not None
    }
    return sorted(result)


def building_card(number: int, language: str = "uk") -> str | None:
    if number not in BUILDINGS:
        return None
    language = normalize_language(language)
    address, directions = BUILDINGS[number][language]
    return get_msg(
        "campus.card",
        language=language,
        number=number,
        address=escape(address),
        directions=escape(directions),
        example=f"{'K' if language == 'en' else 'К'}{number}-101",
    )
