from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    label: str
    href: str
    prefixes: tuple[str, ...]

    def as_template_dict(self) -> dict[str, object]:
        return {"label": self.label, "href": self.href, "prefixes": list(self.prefixes)}


@dataclass(frozen=True)
class RouteZone:
    code: str
    prefixes: tuple[str, ...]
    primary_nav: NavItem | None = None

    def matches(self, path: str) -> bool:
        return any(path == prefix or path.startswith(f"{prefix}/") for prefix in self.prefixes)


INTAKE_NAV = NavItem("Рабочий стол", "/intake", ("/intake", "/reviews"))
CATALOG_NAV = NavItem("Справочник", "/catalog-admin/groups", ("/catalog-admin", "/competencies", "/profiles"))
CURRICULUM_NAV = NavItem("УП", "/up", ("/up",))

ROUTE_ZONES: tuple[RouteZone, ...] = (
    RouteZone("intake", ("/intake",), INTAKE_NAV),
    RouteZone("reviews", ("/reviews",), INTAKE_NAV),
    RouteZone("catalog", ("/catalog-admin", "/competencies", "/profiles"), CATALOG_NAV),
    RouteZone("curriculum", ("/up",), CURRICULUM_NAV),
)

MAIN_NAV: tuple[NavItem, ...] = (INTAKE_NAV, CATALOG_NAV, CURRICULUM_NAV)


@dataclass(frozen=True)
class EcosystemNavItem:
    """A cross-module link (absolute href) shown on every surface's top bar."""

    label: str
    href: str
    code: str


# The one canonical set of module links, identical on generator, catalog and
# auditor, so the platform reads as a single ecosystem. Hrefs are absolute
# (not under the catalog ``base`` prefix).
ECOSYSTEM_NAV: tuple[EcosystemNavItem, ...] = (
    EcosystemNavItem("Главная", "/app", "home"),
    EcosystemNavItem("Генерация", "/app/generate", "generate"),
    EcosystemNavItem("Аудитор", "/app/auditor", "auditor"),
    EcosystemNavItem("Перевод", "/app/translate", "translate"),
    EcosystemNavItem("УП", "/app/curriculum", "curriculum"),
    EcosystemNavItem("Справочник", "/app/spravochnik", "catalog"),
    EcosystemNavItem("Инструкция", "/app/instruction", "instruction"),
)


def get_ecosystem_nav(active_code: str = "catalog") -> list[dict[str, object]]:
    """Cross-module nav for templates; ``active_code`` marks the current surface."""

    return [
        {"label": item.label, "href": item.href, "active": item.code == active_code}
        for item in ECOSYSTEM_NAV
    ]

CATALOG_SECONDARY_NAV: tuple[NavItem, ...] = (
    NavItem("Skills и индикаторы", "/catalog-admin/groups", ("/catalog-admin/groups", "/catalog-admin/skills")),
    NavItem("Компетенции", "/competencies", ("/competencies",)),
    NavItem("Кандидатные компетенции", "/catalog-admin/candidate-competencies", ("/catalog-admin/candidate-competencies",)),
    NavItem("Профили", "/profiles", ("/profiles",)),
    NavItem("Шаблоны УП", "/catalog-admin/artifact-templates", ("/catalog-admin/artifact-templates",)),
    NavItem("Архив", "/catalog-admin/archive", ("/catalog-admin/archive",)),
)


def detect_route_zone(path: str) -> str:
    for zone in ROUTE_ZONES:
        if zone.matches(path):
            return zone.code
    return "unknown"


def get_main_nav() -> list[dict[str, object]]:
    return [item.as_template_dict() for item in MAIN_NAV]


def get_secondary_nav(path: str) -> list[dict[str, object]]:
    if detect_route_zone(path) != "catalog":
        return []
    return [item.as_template_dict() for item in CATALOG_SECONDARY_NAV]


def show_secondary_nav(path: str) -> bool:
    return bool(get_secondary_nav(path))
