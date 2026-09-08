import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useApi } from "./useApi";

describe("useApi", () => {
  it("pasa de loading a data", async () => {
    const { result } = renderHook(() => useApi(() => Promise.resolve(42), []));
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toBe(42);
    expect(result.current.error).toBeNull();
  });

  it("captura el error del fetcher", async () => {
    const { result } = renderHook(() => useApi(() => Promise.reject(new Error("boom")), []));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("boom");
    expect(result.current.data).toBeNull();
  });

  it("al recargar no vuelve a loading: la página no debe vaciarse", async () => {
    // Las páginas hacen `if (loading) return <p>Cargando…</p>`. Si una recarga
    // volviera a loading, el contenido se sustituiría por una línea, el
    // documento se encogería y el navegador perdería la posición del scroll:
    // es lo que hacía que cada acción pareciera saltar arriba.
    let n = 0;
    const { result } = renderHook(() => useApi(() => Promise.resolve(++n), []));
    await waitFor(() => expect(result.current.loading).toBe(false));

    const recarga = result.current.reload();
    const volvioALoading = result.current.loading;
    await recarga;

    expect(volvioALoading).toBe(false);
    await waitFor(() => expect(result.current.data).toBe(2));
  });
});
