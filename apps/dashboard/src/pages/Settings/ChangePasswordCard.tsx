import { useState, type FormEvent } from "react";
import type { AxiosError } from "axios";
import { toast } from "sonner";
import { KeyRound } from "lucide-react";
import api from "@/lib/api";
import { setRefreshToken, setToken } from "@/lib/tokenStore";

type ApiErrorResponse = { detail?: string | { msg: string }[] };

interface AuthTokenResponse {
  access_token: string;
  refresh_token: string;
}

function errorMessage(error: unknown): string {
  const detail = (error as AxiosError<ApiErrorResponse>).response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg).join(", ");
  return "Could not change the password";
}

/**
 * Changing the password ends the user's other sessions on the gateway. The
 * response carries fresh tokens so this browser stays signed in.
 */
export default function ChangePasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const mismatch = confirm.length > 0 && next !== confirm;
  const canSubmit = current.length > 0 && next.length >= 8 && next === confirm && !submitting;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      const { data } = await api.post<AuthTokenResponse>("/auth/password", {
        current_password: current,
        new_password: next,
      });
      setToken(data.access_token);
      setRefreshToken(data.refresh_token);
      setCurrent("");
      setNext("");
      setConfirm("");
      toast.success("Password changed. Your other sessions were signed out.");
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  };

  const inputClass = "w-full px-3 py-2 border rounded-md bg-background text-sm";
  return (
    <div className="bg-card rounded-lg border shadow-sm p-6">
      <h2 className="text-sm font-semibold uppercase tracking-wider flex items-center gap-2 text-muted-foreground mb-6">
        <KeyRound className="w-4 h-4" /> Change Password
      </h2>
      <form onSubmit={submit} className="space-y-3 max-w-sm">
        <label className="block text-sm">
          Current password
          <input type="password" autoComplete="current-password" value={current}
            onChange={(e) => setCurrent(e.target.value)} className={inputClass} />
        </label>
        <label className="block text-sm">
          New password (at least 8 characters)
          <input type="password" autoComplete="new-password" value={next}
            onChange={(e) => setNext(e.target.value)} className={inputClass} />
        </label>
        <label className="block text-sm">
          Confirm new password
          <input type="password" autoComplete="new-password" value={confirm}
            onChange={(e) => setConfirm(e.target.value)} className={inputClass} />
        </label>
        {mismatch && <p className="text-sm text-red-600">The new passwords do not match.</p>}
        <button type="submit" disabled={!canSubmit}
          className="bg-primary text-primary-foreground hover:bg-primary/90 px-4 py-2 rounded-md text-sm font-medium transition-colors disabled:opacity-50">
          {submitting ? "Changing..." : "Change password"}
        </button>
      </form>
    </div>
  );
}
