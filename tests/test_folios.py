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
