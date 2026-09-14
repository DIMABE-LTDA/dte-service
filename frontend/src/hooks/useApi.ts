import { useEffect, useRef, useState } from "react";

export interface ApiState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

/** Carga datos con estados uniformes de loading/error y un `reload()`.
 * El fetcher se lee por ref para mantener el efecto estable; el efecto se
 * vuelve a disparar cuando cambian los `deps` del llamador. */
export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[] = []): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  // Se lleva en ref y no sólo en estado porque `reload` necesita saber si ya
  // hay algo pintado antes de que React aplique el siguiente render.
  const hayDatos = useRef(false);

  async function reload(): Promise<void> {
    // `loading` significa "todavía no hay nada que mostrar", no "hay una
    // petición en curso". La diferencia importa: las páginas hacen
    // `if (loading) return <p>Cargando…</p>`, así que ponerlo en true durante
    // una recarga sustituía la página entera por una línea de texto. El
    // documento se encogía, el navegador perdía la posición del scroll, y al
    // volver a pintar el operador aparecía arriba del todo sin ninguna señal
    // de qué había pasado con la acción que acababa de lanzar.
    if (!hayDatos.current) setLoading(true);
    setError(null);
    try {
      setData(await fetcherRef.current());
      hayDatos.current = true;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, loading, error, reload };
}
