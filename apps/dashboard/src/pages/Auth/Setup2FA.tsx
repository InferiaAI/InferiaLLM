import { useCallback, useEffect, useState } from "react";
import type { AxiosError } from "axios";
import { useNavigate } from "react-router-dom";
import api from "@/lib/api";
import { toast } from "sonner";
import {
  ArrowRight,
  CheckCircle2,
  KeyRound,
  Loader2,
  QrCode,
  RefreshCcw,
  Sparkles,
} from "lucide-react";
import { useAuth } from "@/context/AuthContext";

interface TOTPSetupResponse {
  secret: string;
  qr_code: string;
}

interface ValidationErrorDetail {
  msg?: string;
}

export default function Setup2FA() {
  const navigate = useNavigate();
  const { logout, refreshUser } = useAuth();
  const [isLoading, setIsLoading] = useState(true);
  const [setupData, setSetupData] = useState<TOTPSetupResponse | null>(null);
  const [verifyCode, setVerifyCode] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [setupError, setSetupError] = useState<string | null>(null);

  const getApiErrorMessage = (error: unknown, fallback: string) => {
    const axiosError = error as AxiosError<{ detail?: string | ValidationErrorDetail[] }>;
    const detail = axiosError.response?.data?.detail;

    if (typeof detail === "string") {
      return detail;
    }

    if (Array.isArray(detail)) {
      const message = detail
        .map((item) => item?.msg)
        .filter((item): item is string => Boolean(item))
        .join(", ");
      return message || fallback;
    }

    return fallback;
  };

  const startSetup = useCallback(async () => {
    setIsLoading(true);
    setSetupError(null);
    try {
      const { data } = await api.post<TOTPSetupResponse>("/auth/totp/setup");
      setSetupData(data);
    } catch (error: unknown) {
      console.error(error);
      const message = getApiErrorMessage(error, "Failed to initialize 2FA setup");
      setSetupError(message);
      toast.error(message);
      setSetupData(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void startSetup();
  }, [startSetup]);

  const verifySetup = async () => {
    if (!verifyCode) return;

    setIsSubmitting(true);
    try {
      await api.post("/auth/totp/verify", { totp_code: verifyCode });
      await refreshUser();
      toast.success("2FA setup complete");
      navigate("/", { replace: true });
    } catch (error: unknown) {
      console.error(error);
      toast.error(getApiErrorMessage(error, "Verification failed"));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="relative w-full max-w-5xl px-4 py-6 sm:px-8">
      <div className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(circle_at_top,_hsl(var(--primary)/0.12),_transparent_56%)]" />

      <div className="overflow-hidden rounded-3xl border border-border/70 bg-card/90 shadow-2xl shadow-black/10 backdrop-blur-xl">
        <div className="grid lg:grid-cols-[1.1fr_0.9fr]">
          <aside className="relative overflow-hidden border-b border-border/70 bg-gradient-to-br from-slate-900 via-slate-900 to-slate-800 p-6 text-slate-100 lg:border-b-0 lg:border-r lg:p-10">
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(36,180,126,0.25),_transparent_45%),radial-gradient(circle_at_bottom_right,_rgba(125,211,252,0.16),_transparent_48%)]" />
            <div className="relative space-y-6">
              <div className="inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em]">
                <Sparkles className="h-3.5 w-3.5" />
                Security Checkpoint
              </div>

              <div className="space-y-3">
                <h1 className="text-3xl font-semibold leading-tight sm:text-4xl">
                  Finalize Two-Factor Authentication
                </h1>
                <p className="max-w-md text-sm text-slate-300 sm:text-base">
                  Protect organization access with one-time code verification before entering the dashboard.
                </p>
              </div>

              <div className="space-y-3">
                <div className="rounded-2xl border border-white/15 bg-white/5 p-4">
                  <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-slate-300">
                    <QrCode className="h-3.5 w-3.5" />
                    Step 1
                  </div>
                  <p className="text-sm text-slate-100">Scan the QR code in your authenticator app.</p>
                </div>
                <div className="rounded-2xl border border-white/15 bg-white/5 p-4">
                  <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-slate-300">
                    <KeyRound className="h-3.5 w-3.5" />
                    Step 2
                  </div>
                  <p className="text-sm text-slate-100">Save the secret key as backup enrollment data.</p>
                </div>
                <div className="rounded-2xl border border-white/15 bg-white/5 p-4">
                  <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-slate-300">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    Step 3
                  </div>
                  <p className="text-sm text-slate-100">Enter a valid 6-digit code to activate 2FA.</p>
                </div>
              </div>
            </div>
          </aside>

          <section className="p-6 sm:p-10">
            <div className="space-y-7">
              <div className="space-y-3">
                <div className="inline-flex rounded-full border border-primary/35 bg-primary/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-primary">
                  Mandatory Step
                </div>
                <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Secure your account</h2>
                <p className="text-sm text-muted-foreground sm:text-base">
                  Complete setup to continue to your deployment workspace.
                </p>
              </div>

              {isLoading ? (
                <div className="flex min-h-[20rem] flex-col items-center justify-center gap-4 rounded-2xl border border-border/70 bg-muted/20 p-6 text-center">
                  <Loader2 className="h-8 w-8 animate-spin text-primary" />
                  <p className="text-sm text-muted-foreground">Initializing 2FA setup...</p>
                </div>
              ) : setupError || !setupData ? (
                <div className="space-y-4 rounded-2xl border border-destructive/30 bg-destructive/5 p-5">
                  <p className="text-sm text-destructive">{setupError || "Failed to load setup data. Please retry."}</p>
                  <button
                    onClick={startSetup}
                    className="inline-flex items-center gap-2 rounded-xl border border-border bg-background/70 px-4 py-2 text-sm font-semibold transition hover:bg-accent"
                  >
                    <RefreshCcw className="h-4 w-4" />
                    Retry setup
                  </button>
                </div>
              ) : (
                <div className="space-y-5">
                  <div className="flex flex-col items-center gap-4 rounded-2xl border border-border/70 bg-muted/20 p-5">
                    <div className="rounded-xl border border-border/60 bg-white p-4 shadow-sm">
                      <img src={setupData.qr_code} alt="2FA QR Code" className="h-48 w-48" />
                    </div>
                    <div className="w-full space-y-1 text-center">
                      <p className="text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">Secret Key</p>
                      <code className="block rounded-lg border border-border bg-background px-3 py-2 font-mono text-sm tracking-[0.08em]">
                        {setupData.secret}
                      </code>
                    </div>
                  </div>

                  <div className="space-y-1.5">
                    <label htmlFor="verification-code" className="block text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                      Verification Code
                    </label>
                    <input
                      id="verification-code"
                      placeholder="Enter 6-digit code"
                      value={verifyCode}
                      onChange={(e) => setVerifyCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                      className="h-12 w-full rounded-xl border border-border bg-background/70 px-3 text-center font-mono text-xl font-semibold tracking-[0.5em] outline-none transition placeholder:tracking-normal focus:border-primary/70 focus:ring-2 focus:ring-primary/20"
                      inputMode="numeric"
                      autoComplete="one-time-code"
                    />
                  </div>

                  <button
                    onClick={verifySetup}
                    disabled={verifyCode.length !== 6 || isSubmitting}
                    className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 font-semibold text-primary-foreground shadow-lg shadow-primary/30 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {isSubmitting ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <>
                        Verify & Continue
                        <ArrowRight className="h-4 w-4" />
                      </>
                    )}
                  </button>
                </div>
              )}

              <div className="border-t border-border/70 pt-4 text-center">
                <button
                  onClick={logout}
                  className="text-sm text-muted-foreground transition hover:text-foreground"
                >
                  Log out and return to sign in
                </button>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
