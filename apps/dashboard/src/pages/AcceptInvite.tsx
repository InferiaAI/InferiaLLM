import { useCallback, useEffect, useState } from "react";
import type { AxiosError } from "axios";
import { useSearchParams, useNavigate } from "react-router-dom";
import { authService, type InviteInfo } from "@/services/authService";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import { Loader2, CheckCircle2, AlertCircle, Mail, ShieldCheck, Sparkles, KeyRound } from "lucide-react";

export default function AcceptInvite() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");
  const navigate = useNavigate();
  const { isAuthenticated, user, login } = useAuth();

  const [inviteInfo, setInviteInfo] = useState<InviteInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const getErrorMessage = (err: unknown, fallback: string) => {
    const axiosError = err as AxiosError<{ detail?: string }>;
    return axiosError.response?.data?.detail || fallback;
  };

  const verifyToken = useCallback(async (inviteToken: string) => {
    try {
      const info = await authService.getInviteInfo(inviteToken);
      setInviteInfo(info);
      setError(null);

      if (isAuthenticated && user && user.email.toLowerCase() !== info.email.toLowerCase()) {
        setError(`This invitation is for ${info.email}, but you are logged in as ${user.email}. Please logout and switch accounts.`);
      }
    } catch {
      setError("Invalid or expired invitation");
    } finally {
      setLoading(false);
    }
  }, [isAuthenticated, user]);

  useEffect(() => {
    if (!token) {
      setError("Missing invitation token");
      setLoading(false);
      return;
    }

    void verifyToken(token);
  }, [token, verifyToken]);

  const handleAccept = async () => {
    if (!token) return;

    setProcessing(true);
    try {
      const response = await authService.acceptInvite(token);
      if (response.access_token) {
        await login(response.access_token);
      }
      toast.success("Invitation accepted!");
      navigate("/dashboard");
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, "Failed to accept"));
    } finally {
      setProcessing(false);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirmPassword) {
      toast.error("Passwords do not match");
      return;
    }
    if (!token) return;

    setProcessing(true);
    try {
      const response = await authService.registerInvite({ token, password });
      await login(response.access_token);
      toast.success("Account created and invitation accepted!");
      navigate("/dashboard");
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, "Registration failed"));
    } finally {
      setProcessing(false);
    }
  };

  const formattedExpiry = inviteInfo?.expires_at
    ? new Date(inviteInfo.expires_at).toLocaleString()
    : "Not available";

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
                Invite Gateway
              </div>

              <div className="space-y-3">
                <h1 className="text-3xl font-semibold leading-tight sm:text-4xl">
                  Join Your Team Workspace with a Secure Invite
                </h1>
                <p className="max-w-md text-sm text-slate-300 sm:text-base">
                  Provisioned access is linked to your invite token and role scope so your onboarding stays controlled and auditable.
                </p>
              </div>

              <div className="space-y-3">
                <div className="rounded-2xl border border-white/15 bg-white/5 p-4">
                  <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-slate-300">
                    <Mail className="h-3.5 w-3.5" />
                    Invited Email
                  </div>
                  <p className="break-all text-sm text-slate-100">{inviteInfo?.email || "Checking invitation..."}</p>
                </div>
                <div className="rounded-2xl border border-white/15 bg-white/5 p-4">
                  <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-slate-300">
                    <ShieldCheck className="h-3.5 w-3.5" />
                    Assigned Role
                  </div>
                  <p className="text-sm capitalize text-slate-100">{inviteInfo?.role || "Member"}</p>
                </div>
                <div className="rounded-2xl border border-white/15 bg-white/5 p-4">
                  <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-slate-300">
                    <KeyRound className="h-3.5 w-3.5" />
                    Invite Expires
                  </div>
                  <p className="text-sm text-slate-100">{formattedExpiry}</p>
                </div>
              </div>
            </div>
          </aside>

          <section className="p-6 sm:p-10">
            {loading ? (
              <div className="flex min-h-[26rem] flex-col items-center justify-center gap-4 text-center">
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
                <div>
                  <h2 className="text-2xl font-semibold tracking-tight">Verifying invitation</h2>
                  <p className="mt-2 text-sm text-muted-foreground">Please wait while we validate your access token.</p>
                </div>
              </div>
            ) : error ? (
              <div className="flex min-h-[26rem] flex-col justify-center space-y-6">
                <div className="inline-flex w-fit rounded-full border border-destructive/40 bg-destructive/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-destructive">
                  Access Issue
                </div>
                <div className="space-y-3">
                  <AlertCircle className="h-9 w-9 text-destructive" />
                  <h2 className="text-2xl font-semibold tracking-tight">Invitation Error</h2>
                  <p className="text-sm text-muted-foreground">{error}</p>
                </div>
                <button
                  onClick={() => navigate("/auth/login")}
                  className="w-full rounded-xl border border-border bg-background/70 px-4 py-2.5 font-semibold transition hover:bg-accent"
                >
                  Go to Login
                </button>
              </div>
            ) : !isAuthenticated ? (
              <div className="space-y-7">
                <div className="space-y-3">
                  <div className="inline-flex rounded-full border border-primary/35 bg-primary/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-primary">
                    Team Invite
                  </div>
                  <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Create account and join</h2>
                  <p className="text-sm text-muted-foreground sm:text-base">
                    This invitation is ready. Set your password to complete onboarding.
                  </p>
                </div>

                <form onSubmit={handleRegister} className="space-y-4">
                  <div className="space-y-1.5">
                    <label htmlFor="password" className="block text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                      Create Password
                    </label>
                    <input
                      id="password"
                      className="w-full rounded-xl border border-border bg-background/70 px-3 py-2.5 outline-none transition focus:border-primary/70 focus:ring-2 focus:ring-primary/20"
                      type="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      required
                      minLength={6}
                      placeholder="Minimum 6 characters"
                      disabled={processing}
                      autoComplete="new-password"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label htmlFor="confirm-password" className="block text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                      Confirm Password
                    </label>
                    <input
                      id="confirm-password"
                      className="w-full rounded-xl border border-border bg-background/70 px-3 py-2.5 outline-none transition focus:border-primary/70 focus:ring-2 focus:ring-primary/20"
                      type="password"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      required
                      minLength={6}
                      disabled={processing}
                      autoComplete="new-password"
                    />
                  </div>

                  <button
                    type="submit"
                    disabled={processing}
                    className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 font-semibold text-primary-foreground shadow-lg shadow-primary/30 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {processing && <Loader2 className="h-4 w-4 animate-spin" />}
                    Create Account & Join
                  </button>
                </form>

                <p className="text-sm text-muted-foreground">
                  Already have an account?{" "}
                  <button
                    onClick={() => navigate(`/auth/login?returnUrl=/auth/accept-invite?token=${token}`)}
                    className="font-semibold text-primary hover:underline"
                  >
                    Login to join instead
                  </button>
                </p>
              </div>
            ) : (
              <div className="space-y-7">
                <div className="space-y-3">
                  <div className="inline-flex rounded-full border border-primary/35 bg-primary/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-primary">
                    Ready to Join
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="rounded-full bg-primary/15 p-2">
                      <CheckCircle2 className="h-5 w-5 text-primary" />
                    </div>
                    <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Accept invitation</h2>
                  </div>
                  <p className="text-sm text-muted-foreground sm:text-base">
                    Continue as <span className="font-semibold text-foreground">{inviteInfo?.email}</span> and join this workspace.
                  </p>
                </div>

                <button
                  onClick={handleAccept}
                  disabled={processing}
                  className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 font-semibold text-primary-foreground shadow-lg shadow-primary/30 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {processing && <Loader2 className="h-4 w-4 animate-spin" />}
                  Join Organization
                </button>

                <button
                  onClick={() => navigate("/dashboard")}
                  className="w-full rounded-xl border border-border bg-background/70 px-4 py-2.5 text-sm font-medium text-muted-foreground transition hover:bg-accent hover:text-foreground"
                >
                  Cancel
                </button>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
