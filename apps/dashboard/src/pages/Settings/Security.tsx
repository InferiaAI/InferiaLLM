import { useEffect, useReducer } from "react";
import api from "@/lib/api";
import { toast } from "sonner";
import { Shield, ShieldCheck, KeyRound } from "lucide-react";
import { LoadingScreen } from "@/components/ui/LoadingScreen";
import type { AxiosError } from "axios";

interface UserInfo {
  totp_enabled: boolean;
}

interface TOTPSetupResponse {
  secret: string;
  qr_code: string;
}

type ApiErrorResponse = {
  detail?: string | { msg: string }[];
};

interface SecurityState {
  isLoading: boolean;
  userInfo: UserInfo | null;
  setupData: TOTPSetupResponse | null;
  verifyCode: string;
  isSetupOpen: boolean;
  isSubmitting: boolean;
}

type SecurityAction =
  | { type: "SET_LOADING"; payload: boolean }
  | { type: "SET_USER_INFO"; payload: UserInfo | null }
  | { type: "SET_SETUP_DATA"; payload: TOTPSetupResponse | null }
  | { type: "SET_VERIFY_CODE"; payload: string }
  | { type: "SET_SETUP_OPEN"; payload: boolean }
  | { type: "SET_SUBMITTING"; payload: boolean }
  | { type: "CLOSE_MODAL" };

const initialState: SecurityState = {
  isLoading: true,
  userInfo: null,
  setupData: null,
  verifyCode: "",
  isSetupOpen: false,
  isSubmitting: false,
};

function securityReducer(state: SecurityState, action: SecurityAction): SecurityState {
  switch (action.type) {
    case "SET_LOADING":
      return { ...state, isLoading: action.payload };
    case "SET_USER_INFO":
      return { ...state, userInfo: action.payload };
    case "SET_SETUP_DATA":
      return { ...state, setupData: action.payload };
    case "SET_VERIFY_CODE":
      return { ...state, verifyCode: action.payload };
    case "SET_SETUP_OPEN":
      return { ...state, isSetupOpen: action.payload };
    case "SET_SUBMITTING":
      return { ...state, isSubmitting: action.payload };
    case "CLOSE_MODAL":
      return { ...state, isSetupOpen: false, setupData: null, verifyCode: "" };
    default:
      return state;
  }
}

export default function Security() {
  const [state, dispatch] = useReducer(securityReducer, initialState);
  const { isLoading, userInfo, setupData, verifyCode, isSetupOpen, isSubmitting } = state;

  const fetchStatus = async () => {
    dispatch({ type: "SET_LOADING", payload: true });
    try {
      const { data } = await api.get<UserInfo>("/auth/me");
      dispatch({ type: "SET_USER_INFO", payload: data });
    } catch (error) {
      console.error("Failed to fetch user info", error);
    } finally {
      dispatch({ type: "SET_LOADING", payload: false });
    }
  };

  useEffect(() => {
    void fetchStatus();
  }, []);

  const closeModal = () => dispatch({ type: "CLOSE_MODAL" });

  const startSetup = async () => {
    try {
      const { data } = await api.post<TOTPSetupResponse>("/auth/totp/setup");
      dispatch({ type: "SET_SETUP_DATA", payload: data });
      dispatch({ type: "SET_SETUP_OPEN", payload: true });
    } catch (error) {
      console.error(error);
      toast.error("Failed to start 2FA setup");
    }
  };

  const verifySetup = async () => {
    if (verifyCode.length !== 6) return;

    dispatch({ type: "SET_SUBMITTING", payload: true });
    try {
      await api.post("/auth/totp/verify", { totp_code: verifyCode });
      toast.success("2FA enabled successfully");
      closeModal();
      await fetchStatus();
    } catch (error) {
      const apiError = error as AxiosError<ApiErrorResponse>;
      const detail = apiError.response?.data?.detail;
      if (typeof detail === "string") {
        toast.error(detail);
      } else if (Array.isArray(detail)) {
        toast.error(detail.map((err) => err.msg).join(", ") || "Verification failed");
      } else {
        toast.error("Verification failed");
      }
    } finally {
      dispatch({ type: "SET_SUBMITTING", payload: false });
    }
  };

  if (isLoading) return <LoadingScreen message="Loading security settings..." />;

  return (
    <div className="space-y-6">
      <div className="rounded-xl border bg-card p-5 shadow-sm">
        <h1 className="text-2xl font-semibold tracking-tight">Security Settings</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Manage two-factor authentication and account-level security requirements.
        </p>
      </div>

      <div className="bg-card rounded-lg border shadow-sm p-6">
        <h2 className="text-sm font-semibold uppercase tracking-wider flex items-center gap-2 text-muted-foreground mb-6">
          <Shield className="w-4 h-4" /> Two-Factor Authentication (2FA)
        </h2>

        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="text-sm font-medium inline-flex items-center gap-2 mb-1">
              {userInfo?.totp_enabled ? <ShieldCheck className="w-4 h-4 text-emerald-500" /> : <KeyRound className="w-4 h-4 text-amber-500" />}
              Status: {userInfo?.totp_enabled ? "Enabled" : "Disabled"}
            </p>
            <p className="text-sm text-muted-foreground max-w-xl">
              Protect your account with TOTP using apps like Google Authenticator, Authy, or 1Password.
            </p>
          </div>

          {userInfo?.totp_enabled ? (
            <div className="flex items-center gap-2 px-4 py-2 bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 rounded-md text-sm font-medium border border-green-200 dark:border-green-900">
              <ShieldCheck className="w-4 h-4" />
              2FA is enabled
            </div>
          ) : (
            <button
              type="button"
              onClick={startSetup}
              className="bg-primary text-primary-foreground hover:bg-primary/90 px-4 py-2 rounded-md text-sm font-medium transition-colors"
            >
              Enable 2FA
            </button>
          )}
        </div>
      </div>

      {isSetupOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
          <div className="bg-background border rounded-lg shadow-lg max-w-md w-full p-6 space-y-4">
            <div className="space-y-1.5">
              <h3 className="text-lg font-semibold">Setup Two-Factor Authentication</h3>
              <p className="text-sm text-muted-foreground">Scan the QR code with your authenticator app, then enter the 6-digit code.</p>
            </div>

            {setupData && (
              <div className="flex flex-col items-center gap-4 py-2">
                <div className="bg-white p-2 rounded-lg border">
                  <img src={setupData.qr_code} alt="2FA QR Code" className="w-48 h-48" />
                </div>
                <div className="text-xs text-muted-foreground text-center">
                  <p className="mb-1">Cannot scan the QR code?</p>
                  <code className="bg-muted px-2 py-1 rounded select-all font-mono">{setupData.secret}</code>
                </div>

                <div className="w-full space-y-2 mt-2">
                  <label htmlFor="verify-code" className="text-sm font-medium">Verification Code</label>
                  <input
                    id="verify-code"
                    placeholder="000000"
                    value={verifyCode}
                    onChange={(event) => dispatch({ type: "SET_VERIFY_CODE", payload: event.target.value.replace(/\D/g, "") })}
                    maxLength={6}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-center text-lg tracking-widest placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  />
                </div>
              </div>
            )}

            <div className="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2">
              <button type="button" onClick={closeModal} className="mt-2 sm:mt-0 px-4 py-2 border rounded-md hover:bg-slate-100 transition-colors">
                Cancel
              </button>
              <button
                type="button"
                onClick={verifySetup}
                disabled={verifyCode.length !== 6 || isSubmitting}
                className="bg-primary text-primary-foreground hover:bg-primary/90 px-4 py-2 rounded-md font-medium disabled:opacity-50 transition-colors"
              >
                {isSubmitting ? "Verifying..." : "Verify & Enable"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
