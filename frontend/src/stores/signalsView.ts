/**
 * Persistent UI state for the signals dashboard.
 *
 * Stage 4C added the heatmap column-selection control. With seven
 * components today and ~10 expected by Stage 5, the table will
 * exceed comfortable desktop widths; this store lets users hide
 * families and persists the selection across navigation via
 * localStorage.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export const ALL_COMPONENTS: ReadonlyArray<{ key: string; label: string }> = [
  { key: "trend_signal", label: "Trend" },
  { key: "carry_signal", label: "Carry" },
  { key: "value_signal", label: "Value" },
  { key: "positioning_signal", label: "Positioning" },
  { key: "dislocation_signal", label: "Dislocation" },
  { key: "factor_exposure_signal", label: "Factor exposure" },
  { key: "catalyst_signal", label: "Catalyst" },
  { key: "vol_surface_signal", label: "Vol surface" },
  { key: "nowcasting_signal", label: "Nowcasting" },
  { key: "alt_data_signal", label: "Alt data" },
];

interface SignalsViewState {
  /** Set of component keys currently shown in the heatmap. */
  visibleComponents: string[];
  /** Add or remove a component from the visible set. */
  toggleComponent: (key: string) => void;
  /** Show every registered component. */
  showAll: () => void;
  /** Reset to whatever defaults match the current ALL_COMPONENTS list. */
  resetToDefaults: () => void;
}

export const useSignalsViewStore = create<SignalsViewState>()(
  persist(
    (set, get) => ({
      visibleComponents: ALL_COMPONENTS.map((c) => c.key),
      toggleComponent: (key: string) => {
        const cur = new Set(get().visibleComponents);
        if (cur.has(key)) {
          cur.delete(key);
        } else {
          cur.add(key);
        }
        set({ visibleComponents: Array.from(cur) });
      },
      showAll: () => set({ visibleComponents: ALL_COMPONENTS.map((c) => c.key) }),
      resetToDefaults: () =>
        set({ visibleComponents: ALL_COMPONENTS.map((c) => c.key) }),
    }),
    { name: "macro-trader.signals-view" },
  ),
);
