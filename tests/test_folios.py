import base64

import pytest
from dte_chile import FoliosExhausted

from app.errors.exceptions import DomainError
from app.services import customer_service, folio_service
from tests.conftest import fake_caf_xml, make_customer


def _add_caf(db, customer, doc_type, folio_from, folio_to):
    xml_b64 = base64.b64encode(fake_caf_xml(doc_type, folio_from, folio_to)).decode()
    customer_service.add_caf(db, customer, xml_b64)


def test_next_folio_sequential_and_exhaustion(db):
    customer = make_customer(db)
    _add_caf(db, customer, 33, 1, 3)

    folios = [folio_service.next_folio(db, customer.id, 33)[0] for _ in range(3)]
    assert folios == [1, 2, 3]

    with pytest.raises(FoliosExhausted):
        folio_service.next_folio(db, customer.id, 33)


def test_next_folio_jumps_gap_between_cafs(db):
    customer = make_customer(db)
    _add_caf(db, customer, 33, 1, 2)
    _add_caf(db, customer, 33, 100, 101)

    assigned = [folio_service.next_folio(db, customer.id, 33)[0] for _ in range(4)]
    assert assigned == [1, 2, 100, 101]


def test_no_caf_raises(db):
    customer = make_customer(db)
    from dte_chile import FolioError

    with pytest.raises(FolioError):
        folio_service.next_folio(db, customer.id, 33)


# --------------------------------------------------------------------------- #
#  Retirar un CAF vigente
# --------------------------------------------------------------------------- #
def _caf(db, customer, doc_type, folio_from, folio_to):
    xml_b64 = base64.b64encode(fake_caf_xml(doc_type, folio_from, folio_to)).decode()
    return customer_service.add_caf(db, customer, xml_b64)


def test_retiring_a_caf_moves_the_allocator_to_the_next_range(db):
    """El caso real: llega un CAF que debe reemplazar al que está en uso.

    El asignador siempre toma el rango disponible más bajo, así que sin retirar
    el viejo nunca llegaría a usar el nuevo.
    """
    customer = make_customer(db)
    viejo = _caf(db, customer, 33, 1, 100)
    _caf(db, customer, 33, 101, 105)

    assert folio_service.next_folio(db, customer.id, 33)[0] == 1

    customer_service.retire_caf(db, customer, viejo.id)
    # Salta el resto del rango viejo y entra al nuevo.
    assert folio_service.next_folio(db, customer.id, 33)[0] == 101


def test_folios_already_issued_from_a_retired_caf_stay_issued(db):
    """Retirar corta la emisión futura; no invalida lo ya timbrado."""
    customer = make_customer(db)
    viejo = _caf(db, customer, 33, 1, 100)
    _caf(db, customer, 33, 101, 105)
    folio_service.next_folio(db, customer.id, 33)

    customer_service.retire_caf(db, customer, viejo.id)
    assert customer_service.folio_pointers(db, customer.id)[33] == 1


def test_a_retired_caf_is_not_retired_twice(db):
    customer = make_customer(db)
    caf = _caf(db, customer, 33, 1, 100)
    customer_service.retire_caf(db, customer, caf.id)
    with pytest.raises(DomainError):
        customer_service.retire_caf(db, customer, caf.id)


def test_cannot_retire_a_caf_of_another_customer(db):
    uno = make_customer(db, rut="76158145-7", key="uno")
    dos = make_customer(db, rut="77262159-0", key="dos")
    caf = _caf(db, uno, 33, 1, 100)
    with pytest.raises(DomainError):
        customer_service.retire_caf(db, dos, caf.id)


def test_retiring_the_last_caf_leaves_no_folios(db):
    customer = make_customer(db)
    caf = _caf(db, customer, 33, 1, 100)
    customer_service.retire_caf(db, customer, caf.id)
    with pytest.raises(FoliosExhausted):
        folio_service.next_folio(db, customer.id, 33)


# --------------------------------------------------------------------------- #
#  CAF que el SII rechazaría: llaves, ambiente y vigencia
# --------------------------------------------------------------------------- #
import datetime as dt  # noqa: E402

from app.core import crypto  # noqa: E402
from app.db.models import Caf, FolioAssignment, SiiEnvironment  # noqa: E402

_LONG_AGO = dt.date.today() - dt.timedelta(days=400)


def _b64(xml: bytes) -> str:
    return base64.b64encode(xml).decode()


def test_caf_with_foreign_private_key_is_rejected(db):
    customer = make_customer(db)
    xml = fake_caf_xml(33, 1, 5)
    start, end = xml.index(b"<RSASK>"), xml.index(b"</RSASK>")
    broken = (
        xml[:start]
        + b"<RSASK>-----BEGIN RSA PRIVATE KEY-----\nZHVtbXk=\n"
        + (b"-----END RSA PRIVATE KEY-----" + xml[end:])
    )
    with pytest.raises(DomainError, match="llave privada"):
        customer_service.add_caf(db, customer, _b64(broken))


def test_certification_caf_is_rejected_in_production(db):
    customer = make_customer(db)
    customer.environment = SiiEnvironment.PRODUCTION
    db.commit()
    with pytest.raises(DomainError, match="es de certificación"):
        customer_service.add_caf(db, customer, _b64(fake_caf_xml(33, 1, 5)))
    customer_service.add_caf(db, customer, _b64(fake_caf_xml(33, 1, 5, idk=300)))


def test_expired_credit_caf_is_rejected(db):
    customer = make_customer(db)
    with pytest.raises(DomainError, match="venció"):
        customer_service.add_caf(
            db, customer, _b64(fake_caf_xml(33, 1, 5, authorized_on=_LONG_AGO))
        )


def test_receipt_caf_does_not_expire(db):
    customer = make_customer(db)
    row = customer_service.add_caf(
        db, customer, _b64(fake_caf_xml(39, 1, 5, authorized_on=_LONG_AGO))
    )
    assert row.expires_on is None
    assert folio_service.next_folio(db, customer.id, 39)[0] == 1


def test_upload_stores_dates_and_key_id(db):
    customer = make_customer(db)
    row = customer_service.add_caf(db, customer, _b64(fake_caf_xml(33, 1, 5)))
    assert row.authorized_on == dt.date.today()
    assert row.expires_on is not None and row.expires_on > dt.date.today()
    assert row.key_id == 100


def _legacy_caf(db, customer, doc_type, folio_from, folio_to, authorized_on, idk=100):
    """Un CAF guardado antes de registrar fecha e IDK (columnas nulas)."""
    folio_service.ensure_pointer(db, customer.id, doc_type)
    row = Caf(
        customer_id=customer.id,
        doc_type=doc_type,
        folio_from=folio_from,
        folio_to=folio_to,
        xml_encrypted=crypto.encrypt(
            fake_caf_xml(doc_type, folio_from, folio_to, authorized_on=authorized_on, idk=idk)
        ),
    )
    db.add(row)
    db.commit()
    return row


def test_allocator_skips_an_expired_caf_and_fills_its_dates(db):
    customer = make_customer(db)
    old = _legacy_caf(db, customer, 33, 1, 50, _LONG_AGO)
    _add_caf(db, customer, 33, 51, 60)

    assert folio_service.next_folio(db, customer.id, 33)[0] == 51
    db.refresh(old)
    assert old.authorized_on == _LONG_AGO and old.expires_on is not None


def test_allocator_explains_why_no_folio_is_usable(db):
    customer = make_customer(db)
    _legacy_caf(db, customer, 33, 1, 50, _LONG_AGO)
    with pytest.raises(FoliosExhausted, match="venció"):
        folio_service.next_folio(db, customer.id, 33)


def test_certification_cafs_are_not_used_after_moving_to_production(db):
    customer = make_customer(db)
    _add_caf(db, customer, 33, 1, 5)
    customer.environment = SiiEnvironment.PRODUCTION
    db.commit()
    with pytest.raises(FoliosExhausted, match="certificación"):
        folio_service.next_folio(db, customer.id, 33)


# --------------------------------------------------------------------------- #
#  Inventario y trazabilidad
# --------------------------------------------------------------------------- #
def test_report_lists_states_and_folios_to_review(db):
    customer = make_customer(db)
    _legacy_caf(db, customer, 33, 1, 10, _LONG_AGO)
    _add_caf(db, customer, 33, 11, 20)
    _add_caf(db, customer, 33, 21, 30)
    for _ in range(3):
        folio_service.next_folio(db, customer.id, 33, request_id="req-1")
    folio_service.mark_assignment(db, customer.id, 33, 11, "issued")
    folio_service.mark_assignment(db, customer.id, 33, 12, "failed")
    # El 13 queda asignado y la emisión «se cortó» hace una hora.
    orphan = db.query(FolioAssignment).filter(FolioAssignment.folio == 13).one()
    orphan.created_at = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(hours=1)
    db.commit()

    (report,) = folio_service.folio_report(db, customer)
    assert report["last_folio"] == 13
    assert [c["state"] for c in report["cafs"]] == ["expired", "in_use", "pending"]
    assert [c["unused"] for c in report["cafs"]] == [10, 7, 10]
    assert report["usable_remaining"] == 7 + 10
    assert (report["issued"], report["failed"], report["assigned"]) == (1, 1, 1)
    assert [(r["folio"], r["status"]) for r in report["to_review"]] == [
        (12, "failed"),
        (13, "orphaned"),
    ]


def test_recent_assignment_is_not_reported_as_orphan(db):
    customer = make_customer(db)
    _add_caf(db, customer, 33, 1, 5)
    folio_service.next_folio(db, customer.id, 33)
    (report,) = folio_service.folio_report(db, customer)
    assert report["to_review"] == []


def test_folio_endpoint_only_shows_the_callers_folios(client, db):
    from app.security.service_codes import SERVICE_DTE
    from tests.conftest import grant, headers

    mine = make_customer(db)
    grant(db, mine, SERVICE_DTE)
    _add_caf(db, mine, 33, 1, 5)
    other = make_customer(db, rut="11111111-1", key="cust-2")
    grant(db, other, SERVICE_DTE, apikey="other")
    customer_service.add_caf(db, other, _b64(fake_caf_xml(61, 1, 9, rut="11111111-1")))

    r = client.get("/dte/folios", headers=headers())
    assert r.status_code == 200, r.text
    assert [t["doc_type"] for t in r.json()] == [33]
    r = client.get("/dte/folios", headers=headers("cust-2", "other"))
    assert [t["doc_type"] for t in r.json()] == [61]
