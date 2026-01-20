import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import api from "@/lib/api";
import { toast } from "sonner";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [requiresTwoFactor, setRequiresTwoFactor] = useState(false);
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const payload: any = {
        username: email,
        password,
      };

      if (requiresTwoFactor) {
        payload.totp_code = totpCode;
      }

      const { data } = await api.post("/auth/login", payload);
      await login(data.access_token);
      navigate("/dashboard");
    } catch (error: any) {
      console.error(error);
      const detail = error.response?.data?.detail;

      if (error.response?.status === 403 && detail === "TOTP_REQUIRED") {
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
    <div className="w-full max-w-md p-8 space-y-4 bg-card rounded-lg border shadow-sm">
      <h2 className="text-2xl font-bold text-center">
        {requiresTwoFactor ? "Two-Factor Authentication" : "Sign in"}
      </h2>
      <p className="text-center text-muted-foreground">
        {requiresTwoFactor
          ? "Enter the code from your authenticator app"
          : "Enter your credentials to access the dashboard"}
      </p>

      <form onSubmit={handleSubmit} className="space-y-4">
        {!requiresTwoFactor ? (
          <>
            <div>
              <label className="block text-sm font-medium mb-1">Email</label>
              <input
                className="w-full p-2 border rounded-md bg-background"
                placeholder="name@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                type="email"
                required
                disabled={loading}
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Password</label>
              <input
                className="w-full p-2 border rounded-md bg-background"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                disabled={loading}
              />
            </div>
          </>
        ) : (
          <div>
            <label className="block text-sm font-medium mb-1">2FA Code</label>
            <input
              className="w-full p-2 border rounded-md bg-background text-center text-lg tracking-widest"
              placeholder="000000"
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value)}
              maxLength={6}
              required
              autoFocus
              disabled={loading}
            />
          </div>
        )}

        <button
          disabled={loading}
          className="w-full p-2 bg-primary text-primary-foreground rounded-md font-medium disabled:opacity-50"
        >
          {loading ? "Signing in..." : (requiresTwoFactor ? "Verify & Sign In" : "Sign In")}
        </button>

        {requiresTwoFactor && (
          <button
            type="button"
            onClick={() => {
              setRequiresTwoFactor(false);
              setTotpCode("");
            }}
            className="w-full text-sm text-muted-foreground hover:text-foreground mt-2"
          >
            Back to Login
          </button>
        )}
      </form>
    </div>
  );
}
