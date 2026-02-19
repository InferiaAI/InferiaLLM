import { useCallback, useEffect, useMemo, useState } from "react";
import { rbacService } from "@/services/rbacService";
import type { User, Role, Invitation } from "@/services/rbacService";
import { toast } from "sonner";
import { UserPlus, Shield, Copy, Mail, Users as UsersIcon } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { Pagination } from "@/components/ui/Pagination";
import type { AxiosError } from "axios";

type ApiErrorResponse = {
  detail?: string;
};

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

  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [totalUsers, setTotalUsers] = useState(0);

  const sortedInvitations = useMemo(
    () => [...invitations].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
    [invitations]
  );

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const skip = (currentPage - 1) * pageSize;
      const [usersData, rolesData, invitesData] = await Promise.all([
        rbacService.getUsers({ skip, limit: pageSize }),
        rbacService.getRoles(),
        rbacService.getInvitations(),
      ]);

      setUsers(usersData);
      setTotalUsers(usersData.length === pageSize ? currentPage * pageSize + 1 : (currentPage - 1) * pageSize + usersData.length);
      setRoles(rolesData);
      setInvitations(invitesData);
    } catch (error) {
      console.error(error);
      toast.error("Failed to fetch users or invitations");
    } finally {
      setLoading(false);
    }
  }, [currentPage, pageSize]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const handleRoleChange = async (userId: string, newRole: string) => {
    try {
      await rbacService.updateUserRole(userId, newRole);
      toast.success("User role updated");
      setUsers((prev) => prev.map((user) => (user.id === userId ? { ...user, role: newRole } : user)));
    } catch (error) {
      const apiError = error as AxiosError<ApiErrorResponse>;
      toast.error(apiError.response?.data?.detail || "Update failed");
    }
  };

  const handleInvite = async (event: React.FormEvent) => {
    event.preventDefault();
    setInviting(true);
    try {
      const newInvite = await rbacService.inviteUser(inviteEmail.trim(), inviteRole);
      setInvitations((prev) => [newInvite, ...prev]);
      toast.success("Invitation sent");
      setShowInviteModal(false);
      setInviteEmail("");
      setInviteRole("member");
    } catch (error) {
      const apiError = error as AxiosError<ApiErrorResponse>;
      toast.error(apiError.response?.data?.detail || "Invite failed");
    } finally {
      setInviting(false);
    }
  };

  const handleRevoke = async (id: string) => {
    if (!confirm("Are you sure you want to revoke this invitation?")) return;
    try {
      await rbacService.revokeInvitation(id);
      setInvitations((prev) => prev.filter((invitation) => invitation.id !== id));
      toast.success("Invitation revoked");
    } catch (error) {
      const apiError = error as AxiosError<ApiErrorResponse>;
      toast.error(apiError.response?.data?.detail || "Revoke failed");
    }
  };

  const copyInviteLink = async (link: string) => {
    try {
      await navigator.clipboard.writeText(link);
      toast.success("Invite link copied");
    } catch {
      toast.error("Could not copy invite link");
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-8 w-44 animate-pulse rounded bg-muted" />
        <div className="h-44 animate-pulse rounded-xl border bg-card" />
        <div className="h-44 animate-pulse rounded-xl border bg-card" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div className="rounded-xl border bg-card p-5 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Users & Access</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Manage organization members, assign roles, and control pending invitations.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setShowInviteModal(true)}
            className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            <UserPlus className="w-4 h-4" />
            Invite Member
          </button>
        </div>
      </div>

      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-medium inline-flex items-center gap-2">
            <UsersIcon className="w-4 h-4 text-blue-600 dark:text-blue-400" />
            Active Users
          </h2>
          <span className="text-xs text-muted-foreground">{totalUsers} total</span>
        </div>

        <div className="border rounded-xl bg-card overflow-hidden shadow-sm">
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
              {users.map((user) => (
                <tr key={user.id} className="hover:bg-muted/50 dark:hover:bg-muted/10 transition-colors">
                  <td className="px-6 py-4 font-medium">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center text-primary">
                        <Mail className="w-4 h-4" />
                      </div>
                      <span>{user.email.split("@")[0]}</span>
                      {user.email === currentUser?.email && (
                        <span className="text-xs bg-muted px-1.5 py-0.5 rounded text-muted-foreground">You</span>
                      )}
                    </div>
                  </td>
                  <td className="px-6 py-4 text-muted-foreground">{user.email}</td>
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-2">
                      <Shield className="w-3.5 h-3.5 text-muted-foreground" />
                      <select
                        className="bg-transparent border-none focus:ring-0 p-0 text-sm font-medium cursor-pointer"
                        value={user.role}
                        onChange={(event) => handleRoleChange(user.id, event.target.value)}
                        disabled={user.email === currentUser?.email}
                      >
                        {roles.map((role) => (
                          <option key={role.name} value={role.name}>
                            {role.name}
                          </option>
                        ))}
                      </select>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-muted-foreground">
                    {user.created_at ? new Date(user.created_at).toLocaleDateString() : "-"}
                  </td>
                </tr>
              ))}
              {users.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-6 py-12 text-center text-muted-foreground">
                    No users found on this page.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {users.length > 0 && (
          <Pagination
            currentPage={currentPage}
            totalPages={Math.ceil(totalUsers / pageSize)}
            onPageChange={setCurrentPage}
            pageSize={pageSize}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setCurrentPage(1);
            }}
            totalItems={totalUsers}
          />
        )}
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-medium">Pending Invitations</h2>
        <div className="border rounded-xl bg-card overflow-hidden shadow-sm">
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
              {sortedInvitations.map((invitation) => (
                <tr key={invitation.id} className="hover:bg-muted/50 dark:hover:bg-muted/10 transition-colors">
                  <td className="px-6 py-4 font-medium">{invitation.email}</td>
                  <td className="px-6 py-4 capitalize">{invitation.role}</td>
                  <td className="px-6 py-4">
                    <span className="capitalize bg-muted px-2 py-1 rounded text-xs font-medium border">
                      {invitation.status}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-muted-foreground">{new Date(invitation.created_at).toLocaleDateString()}</td>
                  <td className="px-6 py-4 max-w-[250px]">
                    <code className="block truncate bg-muted px-1.5 py-0.5 rounded text-xs select-all text-muted-foreground font-mono">
                      {invitation.invite_link}
                    </code>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => copyInviteLink(invitation.invite_link)}
                        className="inline-flex items-center gap-1 text-xs rounded border px-2 py-1 hover:bg-muted"
                      >
                        <Copy className="w-3.5 h-3.5" /> Copy
                      </button>
                      <button
                        type="button"
                        onClick={() => handleRevoke(invitation.id)}
                        className="text-destructive hover:underline text-xs"
                      >
                        Revoke
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {sortedInvitations.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-6 py-10 text-center text-muted-foreground">
                    No pending invitations.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

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
                  onChange={(event) => setInviteEmail(event.target.value)}
                  placeholder="colleague@example.com"
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Role</label>
                <select
                  className="w-full p-2 border rounded bg-background"
                  value={inviteRole}
                  onChange={(event) => setInviteRole(event.target.value)}
                >
                  <option value="member">Member</option>
                  <option value="admin">Admin</option>
                </select>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <button type="button" onClick={() => setShowInviteModal(false)} className="px-4 py-2 text-sm font-medium">
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={inviting || inviteEmail.trim().length === 0}
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
