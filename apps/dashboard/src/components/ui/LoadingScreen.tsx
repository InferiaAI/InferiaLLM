import { Loader2 } from "lucide-react";

export function LoadingScreen({ message = "Loading..." }: { message?: string }) {
    return (
        <div className="flex flex-col h-screen w-full items-center justify-center bg-background text-foreground space-y-4">
            <Loader2 className="w-10 h-10 animate-spin text-primary" />
            <p className="text-muted-foreground text-sm font-medium animate-pulse">{message}</p>
        </div>
    );
}
