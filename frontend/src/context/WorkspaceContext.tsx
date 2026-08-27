import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { apiRequest } from "../lib/api";
import type { StateEnvelope, WorkspaceState } from "../types";

interface MutationOptions {
  success?: string;
  refresh?: boolean;
}

interface WorkspaceContextValue {
  state: WorkspaceState | null;
  loading: boolean;
  busy: boolean;
  toast: string;
  refresh: () => Promise<void>;
  signIn: (email: string, password: string) => Promise<void>;
  signInDemo: () => Promise<void>;
  signOut: () => Promise<void>;
  mutate: <T extends Record<string, unknown>>(
    path: string,
    body?: unknown,
    options?: MutationOptions,
  ) => Promise<T>;
  notify: (message: string) => void;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<WorkspaceState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");
  const toastTimer = useRef<number | null>(null);

  const notify = useCallback((message: string) => {
    setToast(message);
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(""), 3200);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const nextState = await apiRequest<WorkspaceState>("/api/state");
      setState(nextState);
    } catch (error) {
      notify(error instanceof Error ? error.message : "Could not load the workspace.");
    } finally {
      setLoading(false);
    }
  }, [notify]);

  useEffect(() => {
    void refresh();
    return () => {
      if (toastTimer.current) window.clearTimeout(toastTimer.current);
    };
  }, [refresh]);

  useEffect(() => {
    const root = document.documentElement;
    const preferences = state?.auth.user?.preferences;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const applyTheme = () => {
      const requested = preferences?.theme || "light";
      root.dataset.theme = requested === "system" ? (media.matches ? "dark" : "light") : requested;
      root.dataset.fontScale = preferences?.font_scale || "comfortable";
      root.dataset.density = preferences?.density || "comfortable";
    };
    applyTheme();
    media.addEventListener("change", applyTheme);
    return () => media.removeEventListener("change", applyTheme);
  }, [state?.auth.user?.preferences]);

  const mutate = useCallback(
    async <T extends Record<string, unknown>>(
      path: string,
      body: unknown = {},
      options: MutationOptions = {},
    ): Promise<T> => {
      setBusy(true);
      try {
        const result = await apiRequest<T>(path, { method: "POST", body });
        const nextState = (result as unknown as StateEnvelope).state;
        if (nextState) {
          setState(nextState);
        } else if (options.refresh) {
          await refresh();
        }
        if (options.success) notify(options.success);
        return result;
      } catch (error) {
        notify(error instanceof Error ? error.message : "The action could not be completed.");
        throw error;
      } finally {
        setBusy(false);
      }
    },
    [notify, refresh],
  );

  const signIn = useCallback(
    async (email: string, password: string) => {
      await mutate<StateEnvelope>("/api/auth/signin", { email, password });
      notify("Welcome back.");
    },
    [mutate, notify],
  );

  const signInDemo = useCallback(async () => {
    await mutate<StateEnvelope>("/api/auth/demo");
    notify("Demo workspace ready.");
  }, [mutate, notify]);

  const signOut = useCallback(async () => {
    await mutate<StateEnvelope>("/api/auth/signout");
    notify("Signed out.");
  }, [mutate, notify]);

  const value = useMemo<WorkspaceContextValue>(
    () => ({ state, loading, busy, toast, refresh, signIn, signInDemo, signOut, mutate, notify }),
    [state, loading, busy, toast, refresh, signIn, signInDemo, signOut, mutate, notify],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const context = useContext(WorkspaceContext);
  if (!context) {
    throw new Error("useWorkspace must be used inside WorkspaceProvider.");
  }
  return context;
}
