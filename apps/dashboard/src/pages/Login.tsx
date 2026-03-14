import { useState } from "react";
import type { AxiosError } from "axios";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import api from "@/lib/api";
import { toast } from "sonner";
import { LockKeyhole, Radar, ShieldCheck, Sparkles } from "lucide-react";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [requiresTwoFactor, setRequiresTwoFactor] = useState(false);
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const payload: {
        username: string;
        password: string;
        totp_code?: string;
      } = {
        username: email,
        password,
      };

      if (requiresTwoFactor) {
        payload.totp_code = totpCode;
      }

      const { data } = await api.post("/auth/login", payload);
      await login(data.access_token, data.refresh_token, data.organizations);
      // Validate returnUrl is a safe relative path (prevents open redirect via //evil.com)
      const rawReturn = searchParams.get("returnUrl");
      const returnUrl =
        rawReturn && /^\/[^/\\]/.test(rawReturn) ? rawReturn : "/dashboard";
      navigate(returnUrl);
    } catch (error: unknown) {
      console.error(error);
      const axiosError = error as AxiosError<{ detail?: string }>;
      const detail = axiosError.response?.data?.detail;

      if (axiosError.response?.status === 403 && detail === "TOTP_REQUIRED") {
        setRequiresTwoFactor(true);
        toast.info("Please enter your 2FA code");
      } else {
        toast.error(detail || "Login failed");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative w-full max-w-5xl px-4 py-6 sm:px-8">
      <div className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(circle_at_top,_hsl(var(--primary)/0.14),_transparent_56%)]" />

      <div className="overflow-hidden rounded-3xl border border-border/70 bg-card/90 shadow-2xl shadow-black/10 backdrop-blur-xl">
        <div className="grid lg:grid-cols-[1.15fr_0.85fr]">
          <aside className="relative overflow-hidden border-b border-border/70 bg-gradient-to-br from-slate-900 via-slate-900 to-slate-800 p-6 text-slate-100 lg:border-b-0 lg:border-r lg:p-10">
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(36,180,126,0.25),_transparent_45%),radial-gradient(circle_at_bottom_right,_rgba(125,211,252,0.18),_transparent_48%)]" />
            <div className="relative space-y-8">
              <div className="inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em]">
                <Sparkles className="h-3.5 w-3.5" />
                Organisation Console
              </div>

              <div className="space-y-4">
                <h1 className="text-3xl font-semibold leading-tight sm:text-4xl">
                  Build, Ship, and Monitor AI Deployments in One Place
                </h1>
                <p className="max-w-md text-sm text-slate-300 sm:text-base">
                  Secure inference operations with policy controls, multi-provider routing, and real-time deployment intelligence.
                </p>
              </div>

              <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-1">
                <div className="rounded-2xl border border-white/15 bg-white/5 p-4">
                  <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-slate-300">
                    <Radar className="h-3.5 w-3.5" />
                    Live Observability
                  </div>
                  <p className="text-sm text-slate-200">Latency and throughput tracked per deployment in real time.</p>
                </div>
                <div className="rounded-2xl border border-white/15 bg-white/5 p-4">
                  <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-slate-300">
                    <ShieldCheck className="h-3.5 w-3.5" />
                    Trust Layer
                  </div>
                  <p className="text-sm text-slate-200">Role-gated access with auditable actions across your organization.</p>
                </div>
                <div className="rounded-2xl border border-white/15 bg-white/5 p-4">
                  <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-slate-300">
                    <LockKeyhole className="h-3.5 w-3.5" />
                    Built-in 2FA
                  </div>
                  <p className="text-sm text-slate-200">Hardware and authenticator-based sign-in hardening by default.</p>
                </div>
              </div>
            </div>
          </aside>

          <section className="p-6 sm:p-10">
            <div className="mb-8 space-y-3">
              <div className="inline-flex rounded-full border border-primary/35 bg-primary/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-primary">
                Team Access
              </div>
              <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">
                {requiresTwoFactor ? "Verify your identity" : "Welcome back"}
              </h2>
              <p className="text-sm text-muted-foreground sm:text-base">
                {requiresTwoFactor
                  ? "Enter the 6-digit authenticator code to finish signing in."
                  : "Sign in to open your deployment workspace."}
              </p>
            </div>

            <form onSubmit={handleSubmit} className="space-y-4">
              {!requiresTwoFactor ? (
                <>
                  <div className="space-y-1.5">
                    <label htmlFor="email" className="block text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                      Work Email
                    </label>
                    <input
                      id="email"
                      className="w-full rounded-xl border border-border bg-background/70 px-3 py-2.5 outline-none transition focus:border-primary/70 focus:ring-2 focus:ring-primary/20"
                      placeholder="name@company.com"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      type="email"
                      required
                      disabled={loading}
                      autoComplete="email"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label htmlFor="password" className="block text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                      Password
                    </label>
                    <input
                      id="password"
                      className="w-full rounded-xl border border-border bg-background/70 px-3 py-2.5 outline-none transition focus:border-primary/70 focus:ring-2 focus:ring-primary/20"
                      type="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      required
                      disabled={loading}
                      autoComplete="current-password"
                    />
                  </div>
                </>
              ) : (
                <div className="space-y-1.5">
                  <label htmlFor="totp-code" className="block text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                    2FA Code
                  </label>
                  <input
                    id="totp-code"
                    className="w-full rounded-xl border border-border bg-background/70 px-3 py-2.5 text-center font-mono text-lg tracking-[0.38em] outline-none transition focus:border-primary/70 focus:ring-2 focus:ring-primary/20"
                    placeholder="000000"
                    value={totpCode}
                    onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                    inputMode="numeric"
                    maxLength={6}
                    required
                    autoFocus
                    disabled={loading}
                    autoComplete="one-time-code"
                  />
                </div>
              )}

              <button
                disabled={loading}
                className="w-full rounded-xl bg-primary px-4 py-2.5 font-semibold text-primary-foreground shadow-lg shadow-primary/30 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {loading ? "Signing in..." : (requiresTwoFactor ? "Verify & Sign In" : "Sign In to Dashboard")}
              </button>

              {requiresTwoFactor && (
                <button
                  type="button"
                  onClick={() => {
                    setRequiresTwoFactor(false);
                    setTotpCode("");
                  }}
                  className="w-full text-sm text-muted-foreground transition hover:text-foreground"
                >
                  Back to email and password
                </button>
              )}
            </form>
          </section>
        </div>
      </div>
    </div>
  );
}
