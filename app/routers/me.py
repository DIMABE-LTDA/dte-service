"""Configuración del propio contribuyente, para el cliente máquina.

Existe porque hay datos del emisor que viven aquí y se usan allá: el número y la
fecha de resolución van en la carátula de **todos** los DTE, y hasta ahora sólo
se veían y editaban desde el portal. Quien integra desde su ERP no tenía forma
de comprobar contra qué está emitiendo, ni de corregirlo sin pedirle a otra
persona que entrara al portal.

Se limita a lo que es dato del propio emisor y afecta a lo que él emite. El
ambiente NO se toca desde aquí: se resuelve al autenticar, a partir de la
credencial usada, y poder cambiarlo con esa misma credencial sería justo el
agujero que la separación por ambiente evita —una apiKey de certificación
pasaría a emitir en producción—.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.models import Customer
from app.db.session import get_db
from app.deps.auth import require_dte

router = APIRouter(prefix="/me", tags=["Configuración"])


class MyConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_code: str
    name: str
    rut: str
    #: CERTIFICATION o PRODUCTION. Sólo lectura: lo fija la credencial.
    environment: str
    resolution_number: int
    resolution_date: dt.date


class MyConfigUpdate(BaseModel):
    """Lo único editable: la resolución que el SII asignó al emisor.

    Ambos campos son opcionales para poder corregir uno sin repetir el otro.
    """

    resolution_number: int | None = Field(default=None, ge=0)
    resolution_date: dt.date | None = None


def _out(customer: Customer) -> MyConfigOut:
    return MyConfigOut(
        customer_code=customer.key,
        name=customer.name,
        rut=customer.rut,
        environment=customer.environment.value
        if hasattr(customer.environment, "value")
        else str(customer.environment),
        resolution_number=customer.resolution_number,
        resolution_date=customer.resolution_date,
    )


@router.get("", response_model=MyConfigOut)
def my_config(
    customer: Customer = Depends(require_dte),
) -> MyConfigOut:
    """Con qué datos está emitiendo esta credencial.

    Sirve para verificar antes de emitir en serie: el ambiente y la resolución
    son los dos datos que, mal puestos, invalidan todos los documentos.
    """
    return _out(customer)


@router.patch("", response_model=MyConfigOut)
def update_my_config(
    body: MyConfigUpdate,
    customer: Customer = Depends(require_dte),
    db: Session = Depends(get_db),
) -> MyConfigOut:
    """Corrige la resolución del emisor.

    Es dato del propio contribuyente y afecta sólo a lo que él emite, así que su
    propia credencial basta. No cambia nada ya emitido: la carátula viaja dentro
    de cada XML firmado.
    """
    if body.resolution_number is not None:
        customer.resolution_number = body.resolution_number
    if body.resolution_date is not None:
        customer.resolution_date = body.resolution_date
    db.commit()
    db.refresh(customer)
    return _out(customer)
