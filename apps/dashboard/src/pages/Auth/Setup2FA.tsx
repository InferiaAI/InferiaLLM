import { useState, useEffect } from "react"
import api from "@/lib/api"
import { toast } from "sonner"
import { Shield, Loader2, ArrowRight } from "lucide-react"
import { useAuth } from "@/context/AuthContext"

interface TOTPSetupResponse {
    secret: string
    qr_code: string
}

export default function Setup2FA() {
    const { logout } = useAuth()
    const [isLoading, setIsLoading] = useState(true)
    const [setupData, setSetupData] = useState<TOTPSetupResponse | null>(null)
    const [verifyCode, setVerifyCode] = useState("")
    const [isSubmitting, setIsSubmitting] = useState(false)

    useEffect(() => {
        // Automatically start setup on mount
        startSetup()
    }, [])

    const startSetup = async () => {
        setIsLoading(true)
        try {
            const { data } = await api.post<TOTPSetupResponse>("/auth/totp/setup")
            setSetupData(data)
        } catch (error) {
            console.error(error)
            toast.error("Failed to Initialize 2FA Setup")
        } finally {
            setIsLoading(false)
        }
    }

    const verifySetup = async () => {
        if (!verifyCode) return
        setIsSubmitting(true)
        try {
            await api.post("/auth/totp/verify", { totp_code: verifyCode })
            toast.success("2FA Setup Complete")
            // Refresh auth state/page to proceed to dashboard
            // Assuming verify endpoint success effectively enables it. 
            // We can navigate to root, and dashboard guard will let us through now that totp_enabled is true.
            // We might need to refresh user info in context? For now, navigation should hit layout check again.
            window.location.href = "/" // Hard reload/redirect to ensure all state is fresh
        } catch (error: any) {
            console.error(error)
            const detail = error.response?.data?.detail
            if (typeof detail === 'string') {
                toast.error(detail)
            } else if (Array.isArray(detail)) {
                toast.error(detail.map((err: any) => err.msg).join(", ") || "Verification failed")
            } else {
                toast.error("Verification failed")
            }
        } finally {
            setIsSubmitting(false)
        }
    }

    return (
        <div className="min-h-screen flex items-center justify-center bg-background p-4">
            <div className="w-full max-w-md p-8 space-y-8 bg-card rounded-lg border shadow-lg">
                <div className="text-center space-y-2">
                    <div className="flex justify-center mb-4">
                        <div className="p-3 bg-primary/10 rounded-full">
                            <Shield className="w-8 h-8 text-primary" />
                        </div>
                    </div>
                    <h1 className="text-2xl font-bold tracking-tight">Secure Your Account</h1>
                    <p className="text-muted-foreground">
                        Enhanced security is required. Please set up Two-Factor Authentication (2FA) to continue.
                    </p>
                </div>

                {isLoading ? (
                    <div className="flex justify-center py-8">
                        <LoadingScreen />
                    </div>
                ) : setupData ? (
                    <div className="space-y-6">
                        <div className="flex flex-col items-center gap-4 py-2">
                            <div className="bg-white p-4 rounded-xl shadow-sm border">
                                <img src={setupData.qr_code} alt="2FA QR Code" className="w-48 h-48" />
                            </div>
                            <div className="text-center space-y-1">
                                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Secret Key</p>
                                <code className="bg-muted px-3 py-1.5 rounded-md select-all font-mono text-sm border">{setupData.secret}</code>
                            </div>
                        </div>

                        <div className="space-y-4">
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Verification Code</label>
                                <input
                                    placeholder="Enter 6-digit code"
                                    value={verifyCode}
                                    onChange={(e) => setVerifyCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                                    className="flex h-12 w-full rounded-md border border-input bg-background px-3 py-2 text-center text-xl tracking-[0.5em] font-mono placeholder:tracking-normal placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 transition-all font-bold"
                                />
                            </div>

                            <button
                                onClick={verifySetup}
                                disabled={!verifyCode || verifyCode.length !== 6 || isSubmitting}
                                className="w-full flex items-center justify-center gap-2 bg-primary text-primary-foreground hover:bg-primary/90 px-4 py-3 rounded-md font-medium disabled:opacity-50 transition-all shadow-sm active:scale-[0.98]"
                            >
                                {isSubmitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <div className="flex items-center gap-2">Verify & Continue <ArrowRight className="w-4 h-4" /></div>}
                            </button>
                        </div>
                    </div>
                ) : (
                    <div className="text-center text-red-500">
                        Failed to load setup data. Please refresh.
                    </div>
                )}

                <div className="pt-4 border-t text-center">
                    <button
                        onClick={logout}
                        className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                    >
                        Log out and return to sign in
                    </button>
                </div>
            </div>
        </div>
    )
}

function LoadingScreen() {
    return <Loader2 className="w-8 h-8 animate-spin text-primary" />
}
