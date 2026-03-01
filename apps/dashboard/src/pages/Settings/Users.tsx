import { useCallback, useEffect, useMemo, useReducer } from "react";
import { rbacService } from "@/services/rbacService";
import type { User, Role, Invitation } from "@/services/rbacService";
import { toast } from "sonner";
import { UserPlus, Shield, Copy, Mail, Users as UsersIcon, Activity } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { Pagination } from "@/components/ui/Pagination";
import type { AxiosError } from "axios";

type ApiErrorResponse = {
  detail?: string;
};

type State = {
  users: User[];
  roles: Role[];
  invitations: Invitation[];
  loading: boolean;
  showInviteModal: boolean;
  inviteEmail: string;
  inviteRole: string;
  inviting: boolean;
  currentPage: number;
  pageSize: number;
  totalUsers: number;
};

type Action =
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'SET_INVITING'; payload: boolean }
  | { type: 'SET_SHOW_MODAL'; payload: boolean }
  | { type: 'SET_FIELD'; field: keyof State; value: any }
  | { type: 'SET_USERS_DATA'; users: User[]; total: number; roles: Role[]; invitations: Invitation[] }
  | { type: 'UPDATE_USER_ROLE'; userId: string; role: string }
  | { type: 'ADD_INVITATION'; invitation: Invitation }
  | { type: 'REMOVE_INVITATION'; id: string };

const initialState: State = {
  users: [],
  roles: [],
  invitations: [],
  loading: true,
  showInviteModal: false,
  inviteEmail: "",
  inviteRole: "member",
  inviting: false,
  currentPage: 1,
  pageSize: 20,
  totalUsers: 0,
};

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'SET_LOADING': return { ...state, loading: action.payload };
    case 'SET_INVITING': return { ...state, inviting: action.payload };
    case 'SET_SHOW_MODAL': return { ...state, showInviteModal: action.payload };
    case 'SET_FIELD': return { ...state, [action.field]: action.value };
    case 'SET_USERS_DATA':
      return {
        ...state,
        users: action.users,
        totalUsers: action.total,
        roles: action.roles,
        invitations: action.invitations,
        loading: false,
      };
    case 'UPDATE_USER_ROLE':
      return {
        ...state,
        users: state.users.map(u => u.id === action.userId ? { ...u, role: action.role } : u)
      };
    case 'ADD_INVITATION':
      return { ...state, invitations: [action.invitation, ...state.invitations] };
    case 'REMOVE_INVITATION':
      return { ...state, invitations: state.invitations.filter(i => i.id !== action.id) };
    default: return state;
  }
}

export default function Users() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const {
    users, roles, invitations, loading, showInviteModal,
    inviteEmail, inviteRole, inviting, currentPage, pageSize, totalUsers
  } = state;

  const { user: currentUser } = useAuth();

  const sortedInvitations = useMemo(
    () => [...invitations].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
    [invitations]
  );

  const fetchData = useCallback(async () => {
    try {
      dispatch({ type: 'SET_LOADING', payload: true });
      const skip = (currentPage - 1) * pageSize;
      const [usersData, rolesData, invitesData] = await Promise.all([
        rbacService.getUsers({ skip, limit: pageSize }),
        rbacService.getRoles(),
        rbacService.getInvitations(),
      ]);

      const total = usersData.length === pageSize ? currentPage * pageSize + 1 : (currentPage - 1) * pageSize + usersData.length;
      dispatch({ type: 'SET_USERS_DATA', users: usersData, total, roles: rolesData, invitations: invitesData });
    } catch (error) {
      console.error(error);
      toast.error("Failed to fetch users or invitations");
      dispatch({ type: 'SET_LOADING', payload: false });
    }
  }, [currentPage, pageSize]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const handleRoleChange = async (userId: string, newRole: string) => {
    try {
      await rbacService.updateUserRole(userId, newRole);
      toast.success("User role updated");
      dispatch({ type: 'UPDATE_USER_ROLE', userId, role: newRole });
    } catch (error) {
      const apiError = error as AxiosError<ApiErrorResponse>;
      toast.error(apiError.response?.data?.detail || "Update failed");
    }
  };

  const handleInvite = async (event: React.FormEvent) => {
    event.preventDefault();
    dispatch({ type: 'SET_INVITING', payload: true });
    try {
      const newInvite = await rbacService.inviteUser(inviteEmail.trim(), inviteRole);
      dispatch({ type: 'ADD_INVITATION', invitation: newInvite });
      toast.success("Invitation sent");
      dispatch({ type: 'SET_SHOW_MODAL', payload: false });
      dispatch({ type: 'SET_FIELD', field: 'inviteEmail', value: "" });
      dispatch({ type: 'SET_FIELD', field: 'inviteRole', value: "member" });
    } catch (error) {
      const apiError = error as AxiosError<ApiErrorResponse>;
      toast.error(apiError.response?.data?.detail || "Invite failed");
    } finally {
      dispatch({ type: 'SET_INVITING', payload: false });
    }
  };

  const handleRevoke = async (id: string) => {
    if (!confirm("Are you sure you want to revoke this invitation?")) return;
    try {
      await rbacService.revokeInvitation(id);
      dispatch({ type: 'REMOVE_INVITATION', id });
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
            onClick={() => dispatch({ type: 'SET_SHOW_MODAL', payload: true })}
            className="inline-flex items-center gap-2 rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700"
          >
            <UserPlus className="w-4 h-4" />
            Invite Member
          </button>
        </div>
      </div>

      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-medium inline-flex items-center gap-2">
            <UsersIcon className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
            Active Users
          </h2>
          <span className="text-xs text-muted-foreground">{totalUsers} total</span>
        </div>

        <div className="border rounded-xl bg-card overflow-hidden shadow-sm">
          <table className="w-full text-sm text-left">
            <thead className="bg-muted/50 text-muted-foreground border-b dark:bg-muted/20">
              <tr>
                <th className="px-6 py-3 font-medium">User</th>
                <th className="px-6 py-3 font-medium">Name</th>
                <th className="px-6 py-3 font-medium">Email</th>
                <th className="px-6 py-3 font-medium">Role</th>
                <th className="px-6 py-3 font-medium">Joined On</th>
                <th className="px-6 py-3 font-medium">Status</th>
                <th className="px-6 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {users.map((user) => (
                <tr key={user.id} className="bg-background hover:bg-muted/50 dark:hover:bg-muted/10 transition-colors">
                  <td className="px-6 py-4 font-medium text-muted-foreground font-mono text-xs">{user.id}</td>
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-3">
                      <div className="w-7 h-7 rounded bg-amber-500 text-black flex items-center justify-center font-bold text-xs shadow-sm">
                        {user.email.charAt(0).toUpperCase()}
                      </div>
                      <span className="text-foreground font-medium">{user.email.split("@")[0].replace(/\./g, ' ')}</span>
                      {user.email === currentUser?.email && (
                        <span className="text-[10px] bg-muted px-1.5 py-0.5 rounded text-muted-foreground uppercase tracking-wider font-semibold">You</span>
                      )}
                    </div>
                  </td>
                  <td className="px-6 py-4 text-muted-foreground">{user.email}</td>
                  <td className="px-6 py-4">
                    <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-purple-500/10 text-purple-600 dark:text-purple-400 text-xs font-semibold relative max-w-[140px]">
                      <UsersIcon className="w-3.5 h-3.5" />
                      <select
                        className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
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
                      <span className="truncate capitalize">{user.role}</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-muted-foreground">
                    {user.created_at ? new Date(user.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "-"}
                  </td>
                  <td className="px-6 py-4">
                    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 text-xs font-medium">
                      <Activity className="w-3 h-3" />
                      Active
                    </span>
                  </td>
                  <td className="px-6 py-4 text-right">
                    <button className="p-1 px-2 rounded hover:bg-muted text-muted-foreground">
                      ...
                    </button>
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
            onPageChange={(p) => dispatch({ type: 'SET_FIELD', field: 'currentPage', value: p })}
            pageSize={pageSize}
            onPageSizeChange={(size) => {
              dispatch({ type: 'SET_FIELD', field: 'pageSize', value: size });
              dispatch({ type: 'SET_FIELD', field: 'currentPage', value: 1 });
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
                <label htmlFor="invite-email" className="block text-sm font-medium mb-1">Email Address</label>
                <input
                  id="invite-email"
                  type="email"
                  required
                  className="w-full p-2 border rounded bg-background"
                  value={inviteEmail}
                  onChange={(event) => dispatch({ type: 'SET_FIELD', field: 'inviteEmail', value: event.target.value })}
                  placeholder="colleague@example.com"
                />
              </div>
              <div>
                <label htmlFor="invite-role" className="block text-sm font-medium mb-1">Role</label>
                <select
                  id="invite-role"
                  className="w-full p-2 border rounded bg-background"
                  value={inviteRole}
                  onChange={(event) => dispatch({ type: 'SET_FIELD', field: 'inviteRole', value: event.target.value })}
                >
                  <option value="member">Member</option>
                  <option value="admin">Admin</option>
                </select>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <button type="button" onClick={() => dispatch({ type: 'SET_SHOW_MODAL', payload: false })} className="px-4 py-2 text-sm font-medium">
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
