import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { authService, type InviteInfo } from "@/services/authService";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import { Loader2, CheckCircle2, AlertCircle } from "lucide-react";

export default function AcceptInvite() {
    const [searchParams] = useSearchParams();
    const token = searchParams.get("token");
    const navigate = useNavigate();
    const { isAuthenticated, user, login } = useAuth();

    const [inviteInfo, setInviteInfo] = useState<InviteInfo | null>(null);
    const [loading, setLoading] = useState(true);
    const [processing, setProcessing] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Registration State
    const [password, setPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");

    useEffect(() => {
        if (!token) {
            setError("Missing invitation token");
            setLoading(false);
            return;
        }
        verifyToken(token);
    }, [token]);

    const verifyToken = async (t: string) => {
        try {
            const info = await authService.getInviteInfo(t);
            setInviteInfo(info);

            if (isAuthenticated && user) {
                if (user.email.toLowerCase() !== info.email.toLowerCase()) {
                    setError(`This invitation is for ${info.email}, but you are logged in as ${user.email}. Please logout and switch accounts.`);
                }
            }
        } catch (err) {
            setError("Invalid or expired invitation");
        } finally {
            setLoading(false);
        }
    };

    const handleAccept = async () => {
        if (!token) return;
        setProcessing(true);
        try {
            await authService.acceptInvite(token);
            toast.success("Invitation accepted!");
            // Force reload to refresh context/orgs usually handled by router/context logic
            navigate("/dashboard");
            window.location.reload();
        } catch (err: any) {
            toast.error(err.response?.data?.detail || "Failed to accept");
        } finally {
            setProcessing(false);
        }
    };

    const handleRegister = async (e: React.FormEvent) => {
        e.preventDefault();
        if (password !== confirmPassword) {
            return toast.error("Passwords do not match");
        }
        if (!token) return;

        setProcessing(true);
        try {
            const response = await authService.registerInvite({ token, password });
            await login(response.access_token);
            toast.success("Account created and invitation accepted!");
            navigate("/dashboard");
        } catch (err: any) {
            toast.error(err.response?.data?.detail || "Registration failed");
        } finally {
            setProcessing(false);
        }
    };

    if (loading) {
        return <div className="flex h-screen items-center justify-center"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>;
    }

    if (error) {
        return (
            <div className="flex h-screen items-center justify-center p-4">
                <div className="max-w-md w-full bg-destructive/10 border border-destructive/20 p-6 rounded-lg text-center space-y-4">
                    <div className="flex justify-center"><AlertCircle className="w-12 h-12 text-destructive" /></div>
                    <h2 className="text-xl font-semibold text-destructive">Invitation Error</h2>
                    <p className="text-sm">{error}</p>
                    <button onClick={() => navigate("/auth/login")} className="underline text-sm opacity-80 hover:opacity-100">
                        Go to Login
                    </button>
                </div>
            </div>
        );
    }

    if (!isAuthenticated) {
        return (
            <div className="flex min-h-screen items-center justify-center p-4">
                <div className="max-w-md w-full bg-card border shadow-sm p-8 rounded-lg space-y-6">
                    <div className="text-center space-y-2">
                        <h2 className="text-2xl font-bold">Accept Invitation</h2>
                        <p className="text-muted-foreground">
                            You've been invited to join an organization.<br />
                            Create your account to accept.
                        </p>
                    </div>

                    <div className="bg-muted p-4 rounded-md text-sm space-y-2">
                        <div className="flex justify-between">
                            <span className="text-muted-foreground">Email:</span>
                            <span className="font-medium text-foreground">{inviteInfo?.email}</span>
                        </div>
                        <div className="flex justify-between">
                            <span className="text-muted-foreground">Role:</span>
                            <span className="font-medium capitalize text-foreground">{inviteInfo?.role}</span>
                        </div>
                    </div>

                    <form onSubmit={handleRegister} className="space-y-4">
                        <div>
                            <label className="block text-sm font-medium mb-1">Create Password</label>
                            <input
                                className="w-full p-2 border rounded-md bg-background"
                                type="password"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                required
                                minLength={6}
                                placeholder="Min. 6 characters"
                                disabled={processing}
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium mb-1">Confirm Password</label>
                            <input
                                className="w-full p-2 border rounded-md bg-background"
                                type="password"
                                value={confirmPassword}
                                onChange={(e) => setConfirmPassword(e.target.value)}
                                required
                                minLength={6}
                                disabled={processing}
                            />
                        </div>

                        <button
                            type="submit"
                            disabled={processing}
                            className="w-full py-2.5 bg-primary text-primary-foreground rounded-md font-medium disabled:opacity-50 flex justify-center items-center gap-2"
                        >
                            {processing && <Loader2 className="w-4 h-4 animate-spin" />}
                            Create Account & Join
                        </button>
                    </form>

                    <div className="text-center text-sm pt-2">
                        <p className="text-muted-foreground">
                            Already have an account? <button onClick={() => navigate(`/auth/login?returnUrl=/auth/accept-invite?token=${token}`)} className="text-primary hover:underline font-medium">Login to Join</button>
                        </p>
                    </div>
                </div>
            </div>
        )
    }

    // Authenticated User Flow (Accept/Join)
    return (
        <div className="flex h-screen items-center justify-center p-4">
            <div className="max-w-md w-full bg-card border shadow-sm p-8 rounded-lg text-center space-y-6">
                <div className="flex justify-center"><div className="bg-primary/10 p-4 rounded-full"><CheckCircle2 className="w-8 h-8 text-primary" /></div></div>

                <div className="space-y-2">
                    <h2 className="text-2xl font-bold">Accept Invitation</h2>
                    <p className="text-muted-foreground">
                        You are accepting an invitation for <strong>{inviteInfo?.email}</strong>.
                    </p>
                </div>

                <div className="bg-muted/30 p-4 rounded text-sm text-left space-y-2">
                    <div className="flex justify-between">
                        <span className="text-muted-foreground">Role:</span>
                        <span className="font-medium capitalize">{inviteInfo?.role || "Member"}</span>
                    </div>
                </div>

                <button
                    onClick={handleAccept}
                    disabled={processing}
                    className="w-full py-2.5 bg-primary text-primary-foreground rounded-md font-medium disabled:opacity-50 flex justify-center items-center gap-2"
                >
                    {processing && <Loader2 className="w-4 h-4 animate-spin" />}
                    Join Organization
                </button>

                <button onClick={() => navigate("/dashboard")} className="text-sm text-muted-foreground hover:underline">
                    Cancel
                </button>
            </div>
        </div>
    );
}
