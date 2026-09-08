import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
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
    <MemoryRouter initialEntries={["/customers/1/certification"]}>
      <Routes>
        <Route path="/customers/:id/certification" element={<Certification />} />
      </Routes>
    </MemoryRouter>,
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
});
