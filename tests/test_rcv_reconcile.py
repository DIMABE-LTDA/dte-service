"""Conciliación del registro del SII contra el ERP del cliente.

Es la funcionalidad que detecta a tiempo lo que si no aparece después como un
libro descuadrado: un documento emitido fuera del ERP, uno cuyo envío se
rechazó sin que nadie lo notara, o dos versiones del mismo con montos
distintos.
"""

import datetime as dt

from dte_chile.rcv import RcvDocument

from app.schemas.rcv import SourceDocumentIn
from app.services import rcv_service

FECHA = dt.date(2026, 9, 2)


def _sii(doc_type=33, folio=1, rut="60803000-K", net=10000, vat=1900, total=11900, exempt=0):
    return RcvDocument(
        operation="VENTA",
        state="REGISTRO",
        doc_type=doc_type,
        folio=folio,
        counterpart_rut=rut,
        counterpart_name="CLIENTE",
        date=FECHA,
        exempt_amount=exempt,
        net_amount=net,
        vat_amount=vat,
        total_amount=total,
    )


def _erp(doc_type=33, folio=1, rut="60803000-K", net=10000, vat=1900, total=11900, exempt=0):
    return SourceDocumentIn(
        doc_type=doc_type,
        folio=folio,
        counterpart_rut=rut,
        date=FECHA,
        exempt_amount=exempt,
        net_amount=net,
        vat_amount=vat,
        total_amount=total,
    )


# --------------------------------------------------------------------------- #
#  Todo cuadra
# --------------------------------------------------------------------------- #
def test_everything_matches():
    r = rcv_service.reconcile([_sii()], [_erp()])
    assert r["balanced"] is True
    assert r["matched"] == 1
    assert r["only_in_sii"] == r["only_in_source"] == r["mismatched"] == []


def test_the_rut_format_does_not_break_the_match():
    """El ERP puede traer el RUT con puntos; el SII no."""
    r = rcv_service.reconcile([_sii(rut="60803000-K")], [_erp(rut="60.803.000-k")])
    assert r["matched"] == 1 and r["balanced"] is True


def test_totals_are_reported_for_both_sides():
    r = rcv_service.reconcile([_sii(total=11900)], [_erp(total=11900)])
    assert r["sii_total"] == r["source_total"] == 11900


# --------------------------------------------------------------------------- #
#  Las tres formas de descuadre
# --------------------------------------------------------------------------- #
def test_a_document_the_erp_does_not_have():
    """Emitido fuera del ERP: el libro del período igual debe declararlo."""
    r = rcv_service.reconcile([_sii(folio=1), _sii(folio=2)], [_erp(folio=1)])

    assert r["balanced"] is False
    assert [d["folio"] for d in r["only_in_sii"]] == [2]
    assert r["only_in_source"] == []


def test_a_document_the_sii_does_not_have():
    """Nunca se envió, o el envío se rechazó y nadie lo notó."""
    r = rcv_service.reconcile([_sii(folio=1)], [_erp(folio=1), _erp(folio=2)])

    assert [d["folio"] for d in r["only_in_source"]] == [2]
    assert r["only_in_sii"] == []


def test_the_same_document_with_different_amounts():
    """El caso peligroso: está en los dos lados y no salta a la vista."""
    r = rcv_service.reconcile(
        [_sii(net=10000, vat=1900, total=11900)], [_erp(net=9000, vat=1710, total=10710)]
    )

    assert r["matched"] == 1
    (diff,) = r["mismatched"]
    assert diff["folio"] == 1
    assert diff["differences"]["net_amount"] == {"sii": 10000, "source": 9000}
    assert diff["differences"]["total_amount"] == {"sii": 11900, "source": 10710}


def test_a_date_difference_is_reported_too():
    """Cambia el período en que hay que declararlo, aunque no mueva el IVA."""
    otro = _erp()
    otro.date = dt.date(2026, 8, 31)
    r = rcv_service.reconcile([_sii()], [otro])

    assert r["mismatched"][0]["differences"]["date"] == {"sii": FECHA, "source": otro.date}


# --------------------------------------------------------------------------- #
#  La llave de cruce
# --------------------------------------------------------------------------- #
def test_the_same_folio_from_different_counterparts_is_not_the_same_document():
    """Distintos emisores repiten correlativos: el folio solo no identifica."""
    r = rcv_service.reconcile([_sii(folio=7, rut="60803000-K")], [_erp(folio=7, rut="76158145-7")])

    assert r["matched"] == 0
    assert len(r["only_in_sii"]) == len(r["only_in_source"]) == 1


def test_the_same_folio_of_different_types_is_not_the_same_document():
    r = rcv_service.reconcile([_sii(doc_type=33, folio=7)], [_erp(doc_type=61, folio=7)])
    assert r["matched"] == 0


# --------------------------------------------------------------------------- #
#  Bordes
# --------------------------------------------------------------------------- #
def test_an_empty_erp_reports_everything_as_missing():
    r = rcv_service.reconcile([_sii(folio=1), _sii(folio=2)], [])
    assert len(r["only_in_sii"]) == 2 and r["balanced"] is False


def test_two_empty_sides_are_balanced():
    r = rcv_service.reconcile([], [])
    assert r["balanced"] is True and r["matched"] == 0


def test_the_result_comes_sorted_so_it_reads_the_same_every_time():
    sii = [_sii(doc_type=61, folio=9), _sii(doc_type=33, folio=4), _sii(doc_type=33, folio=2)]
    r = rcv_service.reconcile(sii, [])
    assert [(d["doc_type"], d["folio"]) for d in r["only_in_sii"]] == [(33, 2), (33, 4), (61, 9)]
