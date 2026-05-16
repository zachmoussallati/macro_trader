import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Calendar from "@/pages/Calendar";
import Data from "@/pages/Data";
import Home from "@/pages/Home";
import Login from "@/pages/Login";
import Methods from "@/pages/Methods";
import Signals from "@/pages/Signals";
import SignalsAltData from "@/pages/SignalsAltData";
import SignalsCatalyst from "@/pages/SignalsCatalyst";
import SignalsDislocation from "@/pages/SignalsDislocation";
import SignalsFactorExposure from "@/pages/SignalsFactorExposure";
import SignalsNowcasting from "@/pages/SignalsNowcasting";
import SignalsPositioning from "@/pages/SignalsPositioning";
import SignalsVolSurface from "@/pages/SignalsVolSurface";
import { useAuthStore } from "@/stores/auth";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const accessToken = useAuthStore((s) => s.accessToken);
  if (!accessToken) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/login" element={<Login />} />
          <Route
            path="/methods"
            element={
              <ProtectedRoute>
                <Methods />
              </ProtectedRoute>
            }
          />
          <Route
            path="/data"
            element={
              <ProtectedRoute>
                <Data />
              </ProtectedRoute>
            }
          />
          <Route
            path="/calendar"
            element={
              <ProtectedRoute>
                <Calendar />
              </ProtectedRoute>
            }
          />
          <Route
            path="/signals"
            element={
              <ProtectedRoute>
                <Signals />
              </ProtectedRoute>
            }
          />
          <Route
            path="/signals/positioning"
            element={
              <ProtectedRoute>
                <SignalsPositioning />
              </ProtectedRoute>
            }
          />
          <Route
            path="/signals/dislocation"
            element={
              <ProtectedRoute>
                <SignalsDislocation />
              </ProtectedRoute>
            }
          />
          <Route
            path="/signals/factor_exposure"
            element={
              <ProtectedRoute>
                <SignalsFactorExposure />
              </ProtectedRoute>
            }
          />
          <Route
            path="/signals/catalyst"
            element={
              <ProtectedRoute>
                <SignalsCatalyst />
              </ProtectedRoute>
            }
          />
          <Route
            path="/signals/vol_surface"
            element={
              <ProtectedRoute>
                <SignalsVolSurface />
              </ProtectedRoute>
            }
          />
          <Route
            path="/signals/nowcasting"
            element={
              <ProtectedRoute>
                <SignalsNowcasting />
              </ProtectedRoute>
            }
          />
          <Route
            path="/signals/alt_data"
            element={
              <ProtectedRoute>
                <SignalsAltData />
              </ProtectedRoute>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
