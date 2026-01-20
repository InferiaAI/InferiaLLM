import { useState, useEffect } from "react"
import api from "@/lib/api"
import { toast } from "sonner"
import { Shield } from "lucide-react"
import { LoadingScreen } from "@/components/ui/LoadingScreen"

interface UserInfo {
    totp_enabled: boolean
}

interface TOTPSetupResponse {
    secret: string
    qr_code: string
}

export default function Security() {
    const [isLoading, setIsLoading] = useState(true)
    const [userInfo, setUserInfo] = useState<UserInfo | null>(null)
    const [setupData, setSetupData] = useState<TOTPSetupResponse | null>(null)
    const [verifyCode, setVerifyCode] = useState("")
    const [isSetupOpen, setIsSetupOpen] = useState(false)
    const [isSubmitting, setIsSubmitting] = useState(false)

    const fetchStatus = async () => {
        setIsLoading(true)
        try {
            const { data } = await api.get("/auth/me")
            setUserInfo(data)
        } catch (error) {
            console.error("Failed to fetch user info", error)
        } finally {
            setIsLoading(false)
        }
    }

    useEffect(() => {
        fetchStatus()
    }, [])

    const startSetup = async () => {
        try {
            const { data } = await api.post<TOTPSetupResponse>("/auth/totp/setup")
            setSetupData(data)
            setIsSetupOpen(true)
        } catch (error) {
            console.error(error)
            toast.error("Failed to start 2FA setup")
        }
    }

    const verifySetup = async () => {
        if (!verifyCode) return
        setIsSubmitting(true)
        try {
            await api.post("/auth/totp/verify", { totp_code: verifyCode })
            toast.success("2FA Enabled Successfully")
            setIsSetupOpen(false)
            setSetupData(null)
            setVerifyCode("")
            fetchStatus() // Refresh status
        } catch (error: any) {
            console.error(error)
            const detail = error.response?.data?.detail
            if (typeof detail === 'string') {
                toast.error(detail)
            } else if (Array.isArray(detail)) {
                // Formatting Pydantic validation errors
                toast.error(detail.map((err: any) => err.msg).join(", ") || "Verification failed")
            } else {
                toast.error("Verification failed")
            }
        } finally {
            setIsSubmitting(false)
        }
    }



    if (isLoading) return <LoadingScreen message="Loading security settings..." />

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <div className="flex items-center text-sm text-muted-foreground mb-2">
                        <span>Settings</span>
                        <span className="mx-2">/</span>
                        <span className="text-foreground font-medium">Security</span>
                    </div>
                    <h1 className="text-3xl font-bold tracking-tight mb-2">Security Settings</h1>
                    <p className="text-muted-foreground">Manage your account security and authentication preferences.</p>
                </div>
            </div>

            <div className="bg-card rounded-lg border shadow-sm p-6">
                <div className="flex items-center gap-2 mb-6">
                    <h3 className="font-mono text-sm font-bold uppercase tracking-wider flex items-center gap-2">
                        <Shield className="w-4 h-4" /> Two-Factor Authentication (2FA)
                    </h3>
                </div>

                <div className="flex items-start justify-between">
                    <div>
                        <p className="text-sm text-foreground mb-1 font-medium">
                            Status: <span className={userInfo?.totp_enabled ? "text-green-500" : "text-yellow-500"}>
                                {userInfo?.totp_enabled ? "Enabled" : "Disabled"}
                            </span>
                        </p>
                        <p className="text-sm text-muted-foreground max-w-xl">
                            Secure your account with TOTP (Time-based One-Time Password) using apps like Google Authenticator, Authy, or 1Password.
                        </p>
                    </div>

                    {userInfo?.totp_enabled ? (
                        <div className="flex items-center gap-2 px-4 py-2 bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 rounded-md text-sm font-medium border border-green-200 dark:border-green-900">
                            <Shield className="w-4 h-4" />
                            2FA is enabled and enforced
                        </div>
                    ) : (
                        <div>
                            {!isSetupOpen ? (
                                <button
                                    onClick={startSetup}
                                    className="bg-primary text-primary-foreground hover:bg-primary/90 px-4 py-2 rounded-md text-sm font-medium transition-colors"
                                >
                                    Enable 2FA
                                </button>
                            ) : (
                                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
                                    <div className="bg-background border rounded-lg shadow-lg max-w-md w-full p-6 space-y-4">
                                        <div className="flex flex-col space-y-1.5 text-center sm:text-left">
                                            <h3 className="text-lg font-semibold leading-none tracking-tight">Setup Two-Factor Authentication</h3>
                                            <p className="text-sm text-muted-foreground">Scan the QR code below with your authenticator app.</p>
                                        </div>

                                        {setupData && (
                                            <div className="flex flex-col items-center gap-4 py-4">
                                                <div className="bg-white p-2 rounded-lg">
                                                    <img src={setupData.qr_code} alt="2FA QR Code" className="w-48 h-48" />
                                                </div>
                                                <div className="text-xs text-muted-foreground text-center">
                                                    <p>Can't scan?</p>
                                                    <code className="bg-muted px-2 py-1 rounded select-all font-mono">{setupData.secret}</code>
                                                </div>

                                                <div className="w-full space-y-2 mt-4">
                                                    <label className="text-sm font-medium">Enter Verification Code</label>
                                                    <input
                                                        placeholder="000000"
                                                        value={verifyCode}
                                                        onChange={(e) => setVerifyCode(e.target.value)}
                                                        maxLength={6}
                                                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-center text-lg tracking-widest ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                                    />
                                                </div>
                                            </div>
                                        )}

                                        <div className="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2">
                                            <button
                                                onClick={() => {
                                                    setIsSetupOpen(false)
                                                    setSetupData(null)
                                                    setVerifyCode("")
                                                }}
                                                className="mt-2 sm:mt-0 px-4 py-2 border rounded-md hover:bg-slate-100 transition-colors"
                                            >
                                                Cancel
                                            </button>
                                            <button
                                                onClick={verifySetup}
                                                disabled={!verifyCode || isSubmitting}
                                                className="bg-primary text-primary-foreground hover:bg-primary/90 px-4 py-2 rounded-md font-medium disabled:opacity-50 transition-colors"
                                            >
                                                {isSubmitting ? "Verifying..." : "Verify & Enable"}
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}
