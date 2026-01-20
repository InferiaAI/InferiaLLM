import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertCircle, RefreshCw } from "lucide-react";

interface Props {
    children?: ReactNode;
}

interface State {
    hasError: boolean;
    error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
    public state: State = {
        hasError: false,
        error: null,
    };

    public static getDerivedStateFromError(error: Error): State {
        return { hasError: true, error };
    }

    public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        console.error("Uncaught error:", error, errorInfo);
    }

    public render() {
        if (this.state.hasError) {
            return (
                <div className="flex h-screen w-full flex-col items-center justify-center bg-background p-4 text-center">
                    <div className="rounded-full bg-destructive/10 p-4 mb-4">
                        <AlertCircle className="h-10 w-10 text-destructive" />
                    </div>
                    <h1 className="text-2xl font-bold tracking-tight text-foreground mb-2">
                        Something went wrong
                    </h1>
                    <p className="text-muted-foreground max-w-md mb-6">
                        An unexpected error occurred. The application has been logged out or encountered a critical failure.
                    </p>
                    <div className="flex gap-4">
                        <button
                            onClick={() => window.location.reload()}
                            className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors"
                        >
                            <RefreshCw className="w-4 h-4" />
                            Reload Application
                        </button>
                        <button
                            onClick={() => {
                                localStorage.clear();
                                window.location.href = "/auth/login";
                            }}
                            className="px-4 py-2 border border-input bg-background hover:bg-accent hover:text-accent-foreground rounded-md transition-colors"
                        >
                            Clear Session & Login
                        </button>
                    </div>
                    {import.meta.env.DEV && (
                        <pre className="mt-8 max-w-2xl overflow-auto rounded bg-muted p-4 text-left text-xs text-muted-foreground">
                            {this.state.error?.toString()}
                        </pre>
                    )}
                </div>
            );
        }

        return this.props.children;
    }
}
