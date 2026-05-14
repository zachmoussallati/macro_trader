import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  email: string | null;
  setTokens: (tokens: { access: string; refresh: string; email?: string }) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      email: null,
      setTokens: ({ access, refresh, email }) =>
        set({ accessToken: access, refreshToken: refresh, email: email ?? null }),
      logout: () => set({ accessToken: null, refreshToken: null, email: null }),
    }),
    { name: "macro-trader-auth" },
  ),
);
