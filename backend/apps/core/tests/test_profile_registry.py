"""S2: registro de proveedores de perfil (roles y unidades) sin depender de otras apps."""

from types import SimpleNamespace

import pytest

from apps.core import profile


@pytest.fixture(autouse=True)
def proveedores_aislados(monkeypatch):
    monkeypatch.setattr(profile, "_providers", [])


def user(is_superuser=False):
    return SimpleNamespace(is_superuser=is_superuser)


def test_sin_proveedores_un_usuario_normal_no_tiene_roles():
    assert profile.collect_profile(user()) == ([], [])


def test_el_superusuario_es_administrador():
    assert profile.collect_profile(user(is_superuser=True)) == (["ADMIN"], [])


def test_reune_roles_y_unidades_de_todos_los_proveedores():
    profile.register_profile_provider(
        lambda u: {"roles": {"OWNER"}, "units": [{"code": "101"}]}
    )
    profile.register_profile_provider(
        lambda u: {"roles": {"BOARD"}, "units": [{"code": "102"}]}
    )

    roles, units = profile.collect_profile(user())

    assert roles == ["BOARD", "OWNER"]
    assert units == [{"code": "101"}, {"code": "102"}]


def test_los_roles_salen_en_orden_fijo_y_los_desconocidos_al_final():
    profile.register_profile_provider(
        lambda u: {"roles": {"TENANT", "ZETA", "ADMIN", "BOARD", "ALFA"}}
    )
    assert profile.collect_profile(user())[0] == [
        "ADMIN",
        "BOARD",
        "TENANT",
        "ALFA",
        "ZETA",
    ]


def test_registrar_dos_veces_el_mismo_proveedor_no_duplica():
    def provider(u):
        return {"roles": {"OWNER"}, "units": [{"code": "101"}]}

    profile.register_profile_provider(provider)
    profile.register_profile_provider(provider)

    assert profile.collect_profile(user())[1] == [{"code": "101"}]


def test_get_roles_devuelve_un_conjunto():
    profile.register_profile_provider(lambda u: {"roles": {"OWNER"}})
    assert profile.get_roles(user(is_superuser=True)) == {"ADMIN", "OWNER"}
