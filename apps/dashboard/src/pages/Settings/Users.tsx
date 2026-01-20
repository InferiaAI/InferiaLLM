import { useEffect, useState } from "react";
import { rbacService } from "@/services/rbacService";
import type { User, Role, Invitation } from "@/services/rbacService";
import { toast } from "sonner";
import { User as UserIcon, Shield } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

export default function Users() {
    const [users, setUsers] = useState<User[]>([]);
    const [roles, setRoles] = useState<Role[]>([]);
    const [invitations, setInvitations] = useState<Invitation[]>([]);
    const [loading, setLoading] = useState(true);
    const { user: currentUser } = useAuth();

    const [showInviteModal, setShowInviteModal] = useState(false);
    const [inviteEmail, setInviteEmail] = useState("");
    const [inviteRole, setInviteRole] = useState("member");
    const [inviting, setInviting] = useState(false);

    useEffect(() => {
        fetchData();
    }, []);

    const fetchData = async () => {
        try {
            const [usersData, rolesData, invitesData] = await Promise.all([
                rbacService.getUsers(),
                rbacService.getRoles(),
                rbacService.getInvitations()
            ]);
            setUsers(usersData);
            setRoles(rolesData);
            setInvitations(invitesData);
        } catch (error) {
            console.error(error);
            toast.error("Failed to fetch users or invitations");
        } finally {
            setLoading(false);
        }
    };

    const handleRoleChange = async (userId: string, newRole: string) => {
        try {
            await rbacService.updateUserRole(userId, newRole);
            toast.success("User role updated");
            setUsers(prev => prev.map(u =>
                u.id === userId ? { ...u, role: newRole } : u
            ));
        } catch (error: any) {
            toast.error(error.response?.data?.detail || "Update failed");
        }
    };

    const handleInvite = async (e: React.FormEvent) => {
        e.preventDefault();
        setInviting(true);
        try {
            const newInvite = await rbacService.inviteUser(inviteEmail, inviteRole);
            setInvitations([...invitations, newInvite]);
            toast.success("Invitation sent");
            setShowInviteModal(false);
            setInviteEmail("");
        } catch (error: any) {
            toast.error(error.response?.data?.detail || "Invite failed");
        } finally {
            setInviting(false);
        }
    };

    const handleRevoke = async (id: string) => {
        if (!confirm("Are you sure you want to revoke this invitation?")) return;
        try {
            await rbacService.revokeInvitation(id);
            setInvitations(prev => prev.filter(i => i.id !== id));
            toast.success("Invitation revoked");
        } catch (error: any) {
            toast.error(error.response?.data?.detail || "Revoke failed");
        }
    };

    if (loading) return <div>Loading...</div>;

    return (
        <div className="space-y-8">
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">Members</h2>
                    <p className="text-muted-foreground">Manage organization users and invitations.</p>
                </div>
                <button
                    onClick={() => setShowInviteModal(true)}
                    className="bg-primary text-primary-foreground px-4 py-2 rounded-md font-medium text-sm flex items-center gap-2 hover:opacity-90 transition-opacity"
                >
                    <UserIcon className="w-4 h-4" />
                    Invite Member
                </button>
            </div>

            {/* Users Table */}
            <div className="space-y-4">
                <h3 className="text-lg font-semibold">Active Users</h3>
                <div className="border rounded-lg bg-card overflow-hidden">
                    <table className="w-full text-sm text-left">
                        <thead className="bg-muted/50 text-muted-foreground border-b dark:bg-muted/20">
                            <tr>
                                <th className="px-6 py-3 font-medium">User</th>
                                <th className="px-6 py-3 font-medium">Email</th>
                                <th className="px-6 py-3 font-medium">Role</th>
                                <th className="px-6 py-3 font-medium">Joined</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y">
                            {users.map(u => (
                                <tr key={u.id} className="hover:bg-muted/50 dark:hover:bg-muted/10 transition-colors">
                                    <td className="px-6 py-4 font-medium flex items-center gap-3">
                                        <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center text-primary">
                                            <UserIcon className="w-4 h-4" />
                                        </div>
                                        <span>{u.email.split('@')[0]}</span>
                                        {u.email === currentUser?.email && (
                                            <span className="text-xs bg-muted px-1.5 py-0.5 rounded text-muted-foreground ml-2">(You)</span>
                                        )}
                                    </td>
                                    <td className="px-6 py-4 text-muted-foreground">{u.email}</td>
                                    <td className="px-6 py-4">
                                        <div className="flex items-center gap-2">
                                            <Shield className="w-3 h-3 text-muted-foreground" />
                                            <select
                                                className="bg-transparent border-none focus:ring-0 p-0 text-sm font-medium cursor-pointer"
                                                value={u.role}
                                                onChange={(e) => handleRoleChange(u.id, e.target.value)}
                                                disabled={u.email === currentUser?.email}
                                            >
                                                {roles.map(r => (
                                                    <option key={r.name} value={r.name}>{r.name}</option>
                                                ))}
                                            </select>
                                        </div>
                                    </td>
                                    <td className="px-6 py-4 text-muted-foreground">
                                        {u.created_at ? new Date(u.created_at).toLocaleDateString() : "-"}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>

            {/* Invitations Table */}
            {invitations.length > 0 && (
                <div className="space-y-4">
                    <h3 className="text-lg font-semibold">Pending Invitations</h3>
                    <div className="border rounded-lg bg-card overflow-hidden">
                        <table className="w-full text-sm text-left">
                            <thead className="bg-muted/50 text-muted-foreground border-b dark:bg-muted/20">
                                <tr>
                                    <th className="px-6 py-3 font-medium">Email</th>
                                    <th className="px-6 py-3 font-medium">Role</th>
                                    <th className="px-6 py-3 font-medium">Status</th>
                                    <th className="px-6 py-3 font-medium">Sent At</th>
                                    <th className="px-6 py-3 font-medium">Invite Link</th>
                                    <th className="px-6 py-3 font-medium">Actions</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y">
                                {invitations.map(inv => (
                                    <tr key={inv.id} className="hover:bg-muted/50 dark:hover:bg-muted/10 transition-colors">
                                        <td className="px-6 py-4 font-medium">{inv.email}</td>
                                        <td className="px-6 py-4">
                                            <span className="capitalize bg-muted px-2 py-1 rounded text-xs font-medium border">{inv.status}</span>
                                        </td>
                                        <td className="px-6 py-4 capitalize">{inv.role}</td>
                                        <td className="px-6 py-4 text-muted-foreground">
                                            {new Date(inv.created_at).toLocaleDateString()}
                                        </td>
                                        <td className="px-6 py-4">
                                            <code className="bg-muted px-1.5 py-0.5 rounded text-xs select-all text-muted-foreground font-mono">
                                                {inv.invite_link}
                                            </code>
                                        </td>
                                        <td className="px-6 py-4">
                                            <button
                                                onClick={() => handleRevoke(inv.id)}
                                                className="text-destructive hover:underline text-xs"
                                            >
                                                Revoke
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {/* Simple Invite Modal */}
            {showInviteModal && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
                    <div className="bg-card w-full max-w-sm rounded-lg border shadow-lg p-6 space-y-4">
                        <h3 className="text-lg font-semibold">Invite New Member</h3>
                        <form onSubmit={handleInvite} className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium mb-1">Email Address</label>
                                <input
                                    type="email"
                                    required
                                    className="w-full p-2 border rounded bg-background"
                                    value={inviteEmail}
                                    onChange={e => setInviteEmail(e.target.value)}
                                    placeholder="colleague@example.com"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium mb-1">Role</label>
                                <select
                                    className="w-full p-2 border rounded bg-background"
                                    value={inviteRole}
                                    onChange={e => setInviteRole(e.target.value)}
                                >
                                    <option value="member">Member</option>
                                    <option value="admin">Admin</option>
                                </select>
                            </div>
                            <div className="flex justify-end gap-2 pt-2">
                                <button
                                    type="button"
                                    onClick={() => setShowInviteModal(false)}
                                    className="px-4 py-2 text-sm font-medium"
                                >
                                    Cancel
                                </button>
                                <button
                                    type="submit"
                                    disabled={inviting}
                                    className="bg-primary text-primary-foreground px-4 py-2 rounded text-sm font-medium disabled:opacity-50"
                                >
                                    {inviting ? "Sending..." : "Send Invite"}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    );
}
