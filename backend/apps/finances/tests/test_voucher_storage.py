"""Configuración del almacenamiento privado de comprobantes."""

import importlib

import pytest


def test_la_ruta_por_defecto_esta_fuera_de_cualquier_ruta_servida(monkeypatch):
    from config.settings import base

    monkeypatch.delenv("VOUCHER_ROOT", raising=False)
    try:
        importlib.reload(base)
        raiz = str(base.VOUCHER_ROOT).replace("\\", "/")
        assert raiz.endswith("var/vouchers")
        assert "static" not in raiz
        assert "media" not in raiz
    finally:
        monkeypatch.undo()
        importlib.reload(base)


def test_voucher_root_se_configura_por_variable_de_entorno(monkeypatch, tmp_path):
    from config.settings import base

    monkeypatch.setenv("VOUCHER_ROOT", str(tmp_path / "volumen"))
    try:
        importlib.reload(base)
        assert str(base.VOUCHER_ROOT) == str(tmp_path / "volumen")
    finally:
        monkeypatch.undo()
        importlib.reload(base)


def test_el_almacenamiento_lee_la_ruta_de_los_settings_en_cada_uso(settings, tmp_path):
    from apps.finances import storage

    almacenamiento = storage.voucher_storage()
    settings.VOUCHER_ROOT = tmp_path / "uno"
    assert almacenamiento.location == str(tmp_path / "uno")
    settings.VOUCHER_ROOT = tmp_path / "dos"
    assert almacenamiento.location == str(tmp_path / "dos")


def test_el_almacenamiento_no_expone_urls():
    from apps.finances import storage

    with pytest.raises(ValueError):
        storage.voucher_storage().url("2026/10/abc.jpg")
