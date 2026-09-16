import { render, screen, waitFor, within } from "@testing-library/react";
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
    // Acotado a la lista: la tabla del formulario de Mi SII repite el estado.
    const lista = within(document.querySelector(".lista-sets") as HTMLElement);
    expect(lista.getByText("Falta declarar")).toBeInTheDocument();
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

  it("pliega la preparación cuando ya no queda nada que hacer ahí", async () => {
    // Verificación, plantilla, importación y receptores se hacen una vez y
    // luego ocupaban ~1200px antes del primer set.
    (api.certChecks as Mock).mockResolvedValue({
      ready: true,
      errors: 0,
      warnings: 0,
      groups: [],
    });
    (api.certDossier as Mock).mockResolvedValue(dossier({ sets: [set()] }));
    mount();

    await screen.findByText("5038170");
    const prep = screen.getByRole("button", { name: /Preparación/ });
    await waitFor(() => expect(prep).toHaveAttribute("aria-expanded", "false"));
    // Plegar no esconde el estado: el veredicto sigue en la cabecera.
    expect(screen.getByText("Verificación OK")).toBeInTheDocument();
  });

  it("deja la preparación abierta mientras la verificación no dé el visto bueno", async () => {
    // No se puede afirmar que esté todo listo si aún no respondió; esconderlo
    // sería afirmarlo.
    (api.certChecks as Mock).mockResolvedValue({
      ready: false,
      errors: 2,
      warnings: 0,
      groups: [],
    });
    (api.certDossier as Mock).mockResolvedValue(dossier({ sets: [set()] }));
    mount();

    await screen.findByText("5038170");
    expect(screen.getByRole("button", { name: /Preparación/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
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

  it("da los datos del formulario de Mi SII en el orden del formulario", async () => {
    // El SII pide N° de envío y fecha por set, en SU orden y con SUS nombres.
    // Estaban repartidos: había que abrir set por set y copiar el TrackID del
    // último envío, diez veces, que es donde se cuela el número equivocado.
    const envio = (id: number, track: string, cuando: string) => ({
      ...set().submissions[0],
      id,
      track_id: track,
      sent_at: cuando,
    });
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({
            id: 1,
            code: "5038171",
            kind: "libro_ventas",
            submissions: [envio(30, "111", "2026-09-15T11:06:52")],
          }),
          set({
            id: 2,
            code: "5038170",
            kind: "basico",
            submissions: [envio(31, "222", "2026-09-14T15:50:00")],
          }),
        ],
      }),
    );
    mount();

    await screen.findByText("Datos para el formulario de Mi SII");
    const tabla = document.querySelectorAll(".card")[1].querySelectorAll("tbody tr");
    // El valor va dentro de `.code`: la celda incluye además la pista "copiar".
    const filas = [...tabla].map((r) =>
      [...r.querySelectorAll("td")]
        .slice(0, 3)
        .map((c) => (c.querySelector(".code") ?? c).textContent),
    );

    // El orden es el del formulario, no el de los datos ni el de los códigos.
    expect(filas[0][0]).toBe("SET BASICO");
    expect(filas[3][0]).toBe("LIBRO DE VENTAS");
    // El básico es el segundo set que llegó, pero va primero.
    expect(filas[0][1]).toBe("222");
    expect(filas[3][1]).toBe("111");
    // Fecha en dd-mm-aaaa, que es el formato que pide el formulario.
    expect(filas[0][2]).toBe("14-09-2026");
    expect(filas[3][2]).toBe("15-09-2026");
    // Un set que el contribuyente aún no tiene se muestra vacío, no se omite:
    // el formulario lo lista igual y hay que saber que falta.
    expect(filas[1][0]).toBe("SET GUIA DE DESPACHO");
    expect(filas[1][1]).toBe("sin envío");
  });

  it("declara varios sets de una vez, y sólo los que el SII aceptó", async () => {
    // En Mi SII el avance se informa de una vez con el formulario entero: ir
    // set por set aquí era repetir diez veces la misma fecha. Pero un set con
    // reparos o rechazado NO entra: declararlo sería informar un avance que no
    // ocurrió, y no se deshace desde aquí.
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({ id: 1, code: "5038170", state: "aceptado" }),
          set({ id: 2, code: "5038175", state: "aceptado" }),
          set({ id: 3, code: "5038180", state: "con_reparos" }),
          set({ id: 4, code: "5038171", state: "declarado", declared_at: "2026-09-14" }),
        ],
      }),
    );
    (api.certDeclare as Mock).mockResolvedValue({});
    const user = userEvent.setup();
    mount();

    // Sólo cuenta los dos aceptados sin declarar.
    await user.click(await screen.findByRole("button", { name: /Declarar 2$/ }));
    const dialogo = await screen.findByRole("dialog");
    expect(dialogo).toHaveTextContent("5038170");
    expect(dialogo).toHaveTextContent("5038175");
    expect(dialogo).not.toHaveTextContent("5038180");

    const fecha = dialogo.querySelector('input[type="date"]') as HTMLInputElement;
    await user.clear(fecha);
    await user.type(fecha, "2026-09-15");
    await user.click(screen.getByRole("button", { name: /Confirmar/ }));

    await waitFor(() => expect(api.certDeclare).toHaveBeenCalledTimes(2));
    expect(api.certDeclare).toHaveBeenCalledWith(1, 1, "2026-09-15");
    expect(api.certDeclare).toHaveBeenCalledWith(1, 2, "2026-09-15");
  });

  it("si un set falla al declararse en masa, lo dice en vez de cantar victoria", async () => {
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({ id: 1, code: "5038170", state: "aceptado" }),
          set({ id: 2, code: "5038175", state: "aceptado" }),
        ],
      }),
    );
    (api.certDeclare as Mock)
      .mockResolvedValueOnce({})
      .mockRejectedValueOnce(Object.assign(new Error("el set ya estaba declarado"), { hints: [] }));
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("button", { name: /Declarar 2$/ }));
    await user.click(screen.getByRole("button", { name: /Confirmar/ }));

    // El otro sí se intentó: un fallo no aborta el resto.
    await waitFor(() => expect(api.certDeclare).toHaveBeenCalledTimes(2));
    // Aparece en el diálogo y además como aviso: en los dos sitios se mira.
    const avisos = await screen.findAllByText(/1 de 2 marcados/);
    expect(avisos.length).toBeGreaterThan(0);
    expect(avisos[0]).toHaveTextContent("5038175");
  });

  it("copia al portapapeles el número de envío al pulsarlo", async () => {
    // userEvent.setup() instala su propio portapapeles de mentira, así que se
    // lee de ahí en vez de sustituirlo: el nuestro quedaría pisado.
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({
            id: 1,
            code: "5038170",
            kind: "basico",
            submissions: [
              { ...set().submissions[0], track_id: "0258723732", sent_at: "2026-09-14T15:50:00" },
            ],
          }),
        ],
      }),
    );
    const user = userEvent.setup();
    mount();

    await screen.findByText("Datos para el formulario de Mi SII");
    await user.click(screen.getByRole("button", { name: /Copiar N.º de envío de SET BASICO/ }));
    expect(await navigator.clipboard.readText()).toBe("0258723732");

    await user.click(screen.getByRole("button", { name: /Copiar fecha de envío de SET BASICO/ }));
    expect(await navigator.clipboard.readText()).toBe("14-09-2026");
  });

  it("para declarar toma el último que el SII ACEPTÓ, no el último enviado", async () => {
    // Un reenvío rechazado queda arriba de la tabla siendo el más nuevo y el
    // que NO sirve. Declararlo manda al Servicio a revisar un sobre que él
    // mismo descartó: pasó de verdad con el libro de compras del set 5038172.
    const envio = (id: number, track: string, estado: string | null) => ({
      ...set().submissions[0],
      id,
      track_id: track,
      sent_at: "2026-09-16T14:47:00",
      sii_state: estado,
      envelope_kind: "LibroCompraVenta",
    });
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({
            id: 1,
            code: "5038172",
            kind: "libro_compras",
            submissions: [
              envio(40, "0258986684", "LOK"), // aceptado
              envio(41, "0258987464", "LNC"), // el SII lo descartó
            ],
          }),
        ],
      }),
    );
    mount();

    await screen.findByText("Datos para el formulario de Mi SII");
    const fila = [...document.querySelectorAll(".card")]
      .find((c) => c.textContent?.includes("Datos para el formulario"))!
      .querySelectorAll("tbody tr")[4]; // LIBRO DE COMPRAS
    expect(fila.textContent).toContain("0258986684");
    expect(fila.textContent).not.toContain("0258987464");
  });

  it("si ningún envío tiene respuesta todavía, vale el último", async () => {
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({
            id: 1,
            code: "5038170",
            kind: "basico",
            submissions: [
              { ...set().submissions[0], id: 50, track_id: "111", sii_state: null },
              { ...set().submissions[0], id: 51, track_id: "222", sii_state: null },
            ],
          }),
        ],
      }),
    );
    mount();

    await screen.findByText("Datos para el formulario de Mi SII");
    const fila = [...document.querySelectorAll(".card")]
      .find((c) => c.textContent?.includes("Datos para el formulario"))!
      .querySelectorAll("tbody tr")[0]; // SET BASICO
    expect(fila.textContent).toContain("222");
  });

  it("cuenta los reparos y los rechazos, que no vienen en progress", async () => {
    // `progress` sólo trae declarados y aceptados: los sets que piden trabajo
    // eran invisibles hasta abrirlos uno por uno.
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({ id: 1, code: "a", state: "aceptado" }),
          set({ id: 2, code: "b", state: "con_reparos" }),
          set({ id: 3, code: "c", state: "rechazado" }),
          set({ id: 4, code: "d", state: "rechazado" }),
          set({ id: 5, code: "e", state: "declarado" }),
        ],
      }),
    );
    mount();

    await screen.findByText("a");
    const cuentas = [...document.querySelectorAll(".avance-cuentas li")].map((li) =>
      li.textContent?.trim(),
    );
    expect(cuentas).toEqual([
      "1aceptados",
      "1con reparos",
      "2rechazados",
      "0sin enviar o sin respuesta",
    ]);
  });

  it("filtra los sets en cliente, sin volver a pedir el expediente", async () => {
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({ id: 1, code: "5038170", state: "aceptado" }),
          set({ id: 2, code: "5038175", state: "rechazado" }),
          set({ id: 3, code: "5038173", state: "declarado" }),
        ],
      }),
    );
    const user = userEvent.setup();
    mount();

    await screen.findByText("5038170");
    expect(api.certDossier).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: /Requieren acción/ }));
    expect(screen.queryByText("5038170")).not.toBeInTheDocument();
    expect(screen.getByText("5038175")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Listos para declarar/ }));
    expect(screen.getByText("5038170")).toBeInTheDocument();
    expect(screen.queryByText("5038175")).not.toBeInTheDocument();

    // Filtrar es presentación: no se vuelve a llamar al API.
    expect(api.certDossier).toHaveBeenCalledTimes(1);
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

  it("muestra sólo el envío vigente y pliega los intentos anteriores", async () => {
    // El set básico acumuló tres sobres: dos caídos enteros y el bueno al
    // final. Los dos primeros ocupaban dos tercios de la tabla repitiendo un
    // "0 de 8 aceptados" que ya no describe la situación.
    const envio = (id: number, track: string, accepted: number) => ({
      ...set().submissions[0],
      id,
      track_id: track,
      stats: [{ doc_type: 33, informed: 8, accepted, rejected: 8 - accepted, flagged: 0 }],
    });
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({
            submissions: [
              envio(10, "0258716370", 0),
              envio(11, "0258720612", 0),
              envio(12, "0258723732", 8),
            ],
          }),
        ],
      }),
    );
    const user = userEvent.setup();
    mount();

    // Acotado a la lista: la tabla del formulario de Mi SII también muestra el
    // TrackID del envío vigente, y aquí se mide lo que hace la tabla del set.
    await screen.findByText("5038170");
    const lista = () => within(document.querySelector(".lista-sets") as HTMLElement);

    // Sólo el último, que es el que describe la situación de hoy.
    expect(lista().getByText("0258723732")).toBeInTheDocument();
    expect(lista().queryByText("0258716370")).not.toBeInTheDocument();

    // Pero no se pierden: se pliegan.
    await user.click(screen.getByRole("button", { name: /2 intentos anteriores/ }));
    expect(lista().getByText("0258716370")).toBeInTheDocument();
    expect(lista().getByText("0258720612")).toBeInTheDocument();
  });

  it("si el último envío es el que falló, es el que se ve", async () => {
    // El corte es "el último", no "los que fallaron": esconder los fallidos
    // escondería justo el que hay que mirar.
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({
            state: "rechazado",
            submissions: [
              { ...set().submissions[0], id: 10, track_id: "0258716370" },
              {
                ...set().submissions[0],
                id: 11,
                track_id: "0258799999",
                stats: [{ doc_type: 33, informed: 8, accepted: 0, rejected: 8, flagged: 0 }],
              },
            ],
          }),
        ],
      }),
    );
    mount();

    await screen.findByText("5038170");
    const lista = within(document.querySelector(".lista-sets") as HTMLElement);
    expect(lista.getByText("0258799999")).toBeInTheDocument();
    expect(lista.getByText("0 de 8 aceptados")).toBeInTheDocument();
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

    // Acotado a la lista: "Preparación" también es un acordeón, y su botón
    // expone aria-expanded igual que el de un set.
    const lista = within(document.querySelector(".lista-sets") as HTMLElement);
    const abiertos = lista.getAllByRole("button", { expanded: true });
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

    // El verbo va en un botón, corto y con affordance; la frase entera se
    // conserva en su título, que es donde no estorba.
    const declarar = await screen.findByRole("button", { name: "Declarar" });
    expect(declarar).toHaveAttribute("title", "Declarar el avance en Mi SII");
    expect(screen.getByRole("button", { name: "Corregir" })).toHaveAttribute(
      "title",
      "Corregir y reenviar",
    );
  });

  it("declara desde la fila plegada, sin tener que abrir el set", async () => {
    // El diálogo vivía dentro del cuerpo expandido: disparado desde una fila
    // plegada cambiaba el estado y no renderizaba nada.
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({ id: 1, code: "5038170", state: "con_reparos" }), // éste se abre solo
          set({ id: 2, code: "5038175", state: "aceptado" }), // éste queda plegado
        ],
      }),
    );
    (api.certDeclare as Mock).mockResolvedValue({});
    const user = userEvent.setup();
    mount();

    await screen.findByText("5038175");
    const fila = screen.getByRole("button", { name: /5038175/ });
    expect(fila).toHaveAttribute("aria-expanded", "false");

    await user.click(screen.getByRole("button", { name: "Declarar" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("5038175");
  });

  it("la fila plegada dice cuántos documentos lleva y cómo le fue", async () => {
    // Sin esto había que abrir el set para saber si valía la pena abrirlo.
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({
            id: 1,
            code: "5038170",
            state: "declarado",
            declared_at: "2026-09-14",
            submissions: [
              {
                ...set().submissions[0],
                documents: [33, 34, 56].map((t, i) => ({ doc_type: t, folio: i })),
                stats: [{ doc_type: 33, informed: 3, accepted: 2, rejected: 1, flagged: 0 }],
              },
            ],
          }),
        ],
      }),
    );
    mount();

    expect(await screen.findByText("3 docs")).toBeInTheDocument();
    expect(screen.getByText("2 aceptados")).toBeInTheDocument();
    expect(screen.getByText("1 rechazados")).toBeInTheDocument();
  });

  it("reserva las mismas ranuras de cifras en todas las filas", async () => {
    // Con celdas elásticas, una fila con reparos empujaba a las de al lado y la
    // columna de cifras se leía en diagonal. Las ranuras que no aplican van
    // vacías, no ausentes.
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({ id: 1, code: "a", state: "declarado", submissions: [] }),
          set({
            id: 2,
            code: "b",
            state: "con_reparos",
            submissions: [
              {
                ...set().submissions[0],
                stats: [{ doc_type: 33, informed: 3, accepted: 1, rejected: 1, flagged: 1 }],
              },
            ],
          }),
        ],
      }),
    );
    mount();

    await screen.findByText("a");
    const filas = [...document.querySelectorAll(".set-cifras")];
    expect(filas).toHaveLength(2);
    for (const f of filas) expect(f.children).toHaveLength(4);
  });

  it("no suma dos veces los documentos de un set reenviado", async () => {
    // Un set rechazado y reenviado tiene dos envíos con los MISMOS documentos
    // dentro: sumarlos diría "6 docs, 6 aceptados" donde hay tres.
    const envio = (id: number, accepted: number) => ({
      ...set().submissions[0],
      id,
      documents: [33, 34, 56].map((t, i) => ({ doc_type: t, folio: i })),
      stats: [{ doc_type: 33, informed: 3, accepted, rejected: 3 - accepted, flagged: 0 }],
    });
    (api.certDossier as Mock).mockResolvedValue(
      dossier({
        sets: [
          set({
            id: 1,
            code: "5038170",
            state: "declarado",
            submissions: [envio(10, 0), envio(11, 3)],
          }),
        ],
      }),
    );
    mount();

    expect(await screen.findByText("3 docs")).toBeInTheDocument();
    // El vigente es el último, no la suma.
    expect(screen.getByText("3 aceptados")).toBeInTheDocument();
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

    // Acotado a la lista: "Preparación" también es un acordeón, y su botón
    // expone aria-expanded igual que el de un set.
    const lista = within(document.querySelector(".lista-sets") as HTMLElement);
    const abiertos = lista.getAllByRole("button", { expanded: true });
    expect(abiertos).toHaveLength(1);
    expect(abiertos[0]).toHaveTextContent("5038175");
  });
});
