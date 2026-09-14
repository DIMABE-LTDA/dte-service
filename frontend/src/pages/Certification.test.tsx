import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ToastProvider } from "../toast";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { api } from "../api";
import Certification from "./Certification";

vi.mock("../api", () => ({
  api: {
    certDossier: vi.fn(),
    certNotes: vi.fn(),
    certDeclare: vi.fn(),
    certRefresh: vi.fn(),
    certAssign: vi.fn(),
    certEnvelope: vi.fn(),
    certAddNote: vi.fn(),
    certSetup: vi.fn(),
    certStep: vi.fn(),
    certDocStatuses: vi.fn(),
    certChecks: vi.fn(),
  },
}));
vi.mock("../auth", async (orig) => {
  const actual = await orig<typeof import("../auth")>();
  return {
    ...actual,
    useAuth: () => ({
      user: { id: 1, email: "op@x.cl", role: "operator", customer_id: null },
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
      refresh: vi.fn(),
    }),
  };
});

function etapas(estados: string[]) {
  const claves = ["requisitos", "emision", "envio", "estado", "declaracion"];
  return claves.map((key, i) => ({ key, label: key, state: estados[i], detail: "" }));
}

function set(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    code: "5038170",
    kind: "basico",
    state: "aceptado",
    declared_at: null,
    stages: etapas(["ok", "ok", "ok", "ok", "atencion"]),
    submissions: [
      {
        id: 10,
        set_id: 1,
        track_id: "0257259806",
        sent_at: "2026-09-02T15:42:00",
        envelope_kind: "EnvioDTE",
        sii_state: "EPR",
        sii_detail: "Envio Procesado",
        checked_at: "2026-09-02T15:50:00",
        documents: [{ doc_type: 33, folio: 19 }],
        stats: [],
      },
    ],
    ...overrides,
  };
}

const PASOS = [
  {
    key: "sets",
    label: "Set de pruebas",
    detail: "0 de 10 declarados",
    automatic: true,
    state: "pendiente",
    done_at: null,
    note: "",
  },
  {
    key: "impresion",
    label: "Muestras de impresión",
    detail: "PDF con timbre",
    automatic: false,
    state: "pendiente",
    done_at: null,
    note: "",
  },
];

function dossier(extra: Record<string, unknown> = {}) {
  return {
    customer_id: 1,
    progress: { sets_total: 10, sets_declared: 0, sets_accepted: 0, sets_pending: 10 },
    steps: PASOS,
    sets: [],
    unassigned: [],
    ...extra,
  };
}

function mount() {
  return render(
    <ToastProvider>
      <MemoryRouter initialEntries={["/customers/1/certification"]}>
        <Routes>
          <Route path="/customers/:id/certification" element={<Certification />} />
        </Routes>
      </MemoryRouter>
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.certNotes as Mock).mockResolvedValue([]);
});

describe("Expediente de certificación", () => {
  it("no ofrece declarar un set que el SII todavía no aceptó", async () => {
    // Declarar un avance que no ocurrió es informarle al SII algo falso, y no
    // se deshace desde aquí.
    (api.certDossier as Mock).mockResolvedValue({
      ...dossier({
        sets: [
          set({ state: "enviado", stages: etapas(["ok", "ok", "ok", "pendiente", "pendiente"]) }),
        ],
      }),
    });
    mount();

    expect(await screen.findByText("5038170")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Marcar declarado/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Consulta el estado en el SII antes de declarar/)).toBeInTheDocument();
  });

  it("avisa de otra forma cuando el SII rechazó el envío", async () => {
    (api.certDossier as Mock).mockResolvedValue({
      ...dossier({
        sets: [
          set({ state: "rechazado", stages: etapas(["ok", "ok", "ok", "error", "pendiente"]) }),
        ],
      }),
    });
    mount();
    expect(await screen.findByText(/corrige y reenvía antes de declarar/)).toBeInTheDocument();
  });

  it("sí lo ofrece cuando está aceptado, y declara con la fecha elegida", async () => {
    (api.certDossier as Mock).mockResolvedValue({
      ...dossier({ sets: [set()] }),
    });
    (api.certDeclare as Mock).mockResolvedValue(set({ state: "declarado" }));
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("button", { name: /Marcar declarado/ }));
    const fecha = document.querySelector('input[type="date"]') as HTMLInputElement;
    await user.clear(fecha);
    await user.type(fecha, "2026-09-02");
    await user.click(screen.getByRole("button", { name: /Confirmar/ }));

    await waitFor(() => expect(api.certDeclare).toHaveBeenCalledWith(1, 1, "2026-09-02"));
  });

  it("traduce los valores internos del API a algo legible", async () => {
    (api.certDossier as Mock).mockResolvedValue({
      ...dossier({ sets: [set({ kind: "libro_ventas" })] }),
    });
    mount();
    // Ni 'libro_ventas' ni 'aceptado' a secas: son valores del modelo.
    expect(await screen.findByText("Libro de ventas")).toBeInTheDocument();
    expect(screen.getByText("aceptado, falta declarar")).toBeInTheDocument();
  });

  it("muestra los envíos sin clasificar y deja asignarlos", async () => {
    (api.certDossier as Mock).mockResolvedValue({
      ...dossier({
        unassigned: [
          {
            id: 20,
            set_id: null,
            track_id: "0257259812",
            sent_at: "2026-09-02T15:43:00",
            envelope_kind: "EnvioDTE",
            sii_state: null,
            sii_detail: null,
            checked_at: null,
            documents: [],
            stats: [],
          },
        ],
      }),
    });
    (api.certAssign as Mock).mockResolvedValue({});
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("button", { name: /Asignar a un set/ }));
    await user.type(screen.getByPlaceholderText("5038170"), "5038173");
    await user.click(screen.getByRole("button", { name: /^Asignar$/ }));

    await waitFor(() => expect(api.certAssign).toHaveBeenCalledWith(1, 20, "5038173", ""));
  });

  it("muestra los sets que faltan por dar de alta, no sólo los enviados", async () => {
    // Un expediente que sólo mostrara lo enviado escondería justo lo que hay
    // que hacer.
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          {
            id: null,
            code: "",
            kind: "basico",
            state: "sin_dar_de_alta",
            declared_at: null,
            stages: [],
            submissions: [],
          },
          {
            id: null,
            code: "",
            kind: "libro_ventas",
            state: "sin_dar_de_alta",
            declared_at: null,
            stages: [],
            submissions: [],
          },
        ],
      }),
    );
    mount();
    expect(await screen.findByText("Sets sin dar de alta")).toBeInTheDocument();
    expect(screen.getByText("Set básico")).toBeInTheDocument();
    expect(screen.getByText("Libro de ventas")).toBeInTheDocument();
  });

  it("deja cerrar y reabrir los pasos que ocurren fuera del servicio", async () => {
    (api.certDossier as Mock).mockResolvedValue(dossier());
    (api.certStep as Mock).mockResolvedValue(dossier());
    const user = userEvent.setup();
    mount();

    // El paso 1 lo lleva el sistema: no debe ofrecer marcarlo a mano.
    const botones = await screen.findAllByRole("button", { name: /Marcar cumplido/ });
    expect(botones).toHaveLength(1);
    await user.click(botones[0]);
    await waitFor(() =>
      expect(api.certStep).toHaveBeenCalledWith(1, "impresion", expect.any(String), ""),
    );
  });

  it("muestra el avance en sets declarados", async () => {
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        progress: { sets_total: 10, sets_declared: 3, sets_accepted: 5, sets_pending: 5 },
      }),
    );
    mount();
    expect(await screen.findByText("3 de 10 sets declarados")).toBeInTheDocument();
  });

  it("la situación registral no se presenta como el motivo de un reparo", async () => {
    // MMC y AND son lo esperado en una factura corregida y en una nota anulada.
    // Presentarlos bajo «esto dice cuál y por qué» hizo reemitir un set que
    // estaba correcto, gastando folios.
    (api.certDossier as Mock).mockResolvedValue(dossier({ sets: [set()] }));
    (api.certDocStatuses as Mock).mockResolvedValue([
      { doc_type: 46, folio: 2, status: "MMC", label: "", error_label: "" },
      { doc_type: 56, folio: 7, status: "DOK", label: "", error_label: "" },
      { doc_type: 110, folio: 8, status: "DNK", label: "", error_label: "" },
    ]);
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("button", { name: /Ver cada documento/ }));
    const dialogo = await screen.findByRole("dialog");

    // Ya no promete el motivo del reparo; dice dónde está de verdad.
    expect(dialogo).toHaveTextContent(/No.*es el motivo de un reparo/i);
    expect(dialogo).toHaveTextContent(/correo del SII/);

    // Y traduce los códigos sin esconderlos.
    expect(dialogo).toHaveTextContent("modificado por una nota de crédito");
    expect(dialogo).toHaveTextContent("MMC");

    // Lo que pide acción va primero, aunque su folio sea el mayor.
    const filas = dialogo.querySelectorAll("tbody tr");
    expect(filas[0]).toHaveTextContent("DNK");
  });

  it("no repite la guía del SII bajo cada envío", async () => {
    // El texto sale del catálogo del código, así que tres envíos con la misma
    // respuesta traían el MISMO párrafo tres veces, y con distinto color —
    // porque el `ok` sí se calcula por envío—. Un instructivo genérico se leía
    // como urgente en un envío y no en otro.
    const causa = {
      label: "Envío procesado",
      meaning: "El sobre se procesó. Ojo: puede traer documentos con reparos dentro.",
      usually: "",
      check: ["Revisa el detalle del envío en Mi SII.", "El SII también avisa por correo."],
      ok: true,
    };
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({
            submissions: [10, 11, 12].map((id) => ({
              id,
              set_id: 1,
              track_id: `02572598${id}`,
              sent_at: "2026-09-02T15:42:00",
              envelope_kind: "EnvioDTE",
              sii_state: "EPR",
              sii_detail: "Envio Procesado",
              checked_at: "2026-09-02T15:50:00",
              documents: [{ doc_type: 33, folio: 19 }],
              // El del medio no quedó entregado: el backend le pone ok=false a
              // ESE, y antes eso pintaba de rojo un texto idéntico a los otros.
              cause: { ...causa, ok: id !== 11 },
              stats: [],
            })),
          }),
        ],
      }),
    );
    mount();

    await screen.findByText("5038170");
    expect(screen.getAllByText(/puede traer documentos con reparos dentro/)).toHaveLength(1);
    expect(screen.getAllByText(/El SII también avisa por correo/)).toHaveLength(1);
  });

  it("dice en texto cuántos documentos aceptó el SII, no sólo con el color", async () => {
    // El color de la insignia era el único canal que distinguía un sobre
    // entregado de uno que sólo se pudo leer.
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({
            submissions: [
              {
                ...set().submissions[0],
                stats: [{ doc_type: 110, informed: 3, accepted: 2, rejected: 0, flagged: 1 }],
              },
            ],
          }),
        ],
      }),
    );
    mount();
    expect(await screen.findByText("2 de 3 aceptados")).toBeInTheDocument();
  });

  it("abre un solo set: el primero que queda por trabajar", async () => {
    // Con los diez expandidos la página medía varios miles de píxeles y no
    // había forma de ver dónde estabas.
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({ id: 1, code: "5038170", state: "declarado", declared_at: "2026-09-14" }),
          set({ id: 2, code: "5038175", state: "con_reparos" }),
          set({ id: 3, code: "5038173", state: "pendiente" }),
        ],
      }),
    );
    mount();

    // Los tres se listan siempre: el que falta es el que importa.
    expect(await screen.findByText("5038170")).toBeInTheDocument();
    expect(screen.getByText("5038173")).toBeInTheDocument();

    const abiertos = screen.getAllByRole("button", { expanded: true });
    expect(abiertos).toHaveLength(1);
    expect(abiertos[0]).toHaveTextContent("5038175");
  });

  it("dice qué toca hacer con cada set, sin tener que deducirlo", async () => {
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({ id: 1, code: "5038170", state: "aceptado" }),
          set({ id: 2, code: "5038175", state: "rechazado" }),
        ],
      }),
    );
    mount();

    expect(await screen.findByText("Declarar el avance en Mi SII")).toBeInTheDocument();
    expect(screen.getByText("Corregir y reenviar")).toBeInTheDocument();
  });

  it("abrir un set cierra el anterior", async () => {
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({ id: 1, code: "5038170", state: "aceptado" }),
          set({ id: 2, code: "5038175", state: "aceptado" }),
        ],
      }),
    );
    const user = userEvent.setup();
    mount();

    await screen.findByText("5038170");
    await user.click(screen.getByRole("button", { name: /5038175/ }));

    const abiertos = screen.getAllByRole("button", { expanded: true });
    expect(abiertos).toHaveLength(1);
    expect(abiertos[0]).toHaveTextContent("5038175");
  });
});
