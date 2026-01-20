import { Link } from "react-router-dom";

export default function Register() {

    return (
        <div className="w-full max-w-md p-8 space-y-4 bg-card rounded-lg border shadow-sm mx-auto text-center">
            <h1 className="text-2xl font-bold tracking-tight">
                Invite Only
            </h1>
            <p className="text-muted-foreground">
                Public registration is currently disabled.<br />
                Please ask an administrator for an invitation link to create an account.
            </p>
            <div className="pt-4">
                <Link to="/auth/login" className="text-primary hover:underline">
                    Back to Login
                </Link>
            </div>
        </div>
    );
}
