import { useCallback, useEffect, useReducer } from "react";
import { rbacService } from "@/services/rbacService";
import type { Role, RoleCreate } from "@/services/rbacService";
import { toast } from "sonner";
import { Plus, Trash2, Edit, ShieldCheck, RefreshCw } from "lucide-react";
import { Pagination } from "@/components/ui/Pagination";
import type { AxiosError } from "axios";

type ApiErrorResponse = {
  detail?: string;
};

type State = {
  roles: Role[];
  permissionsList: string[];
  loading: boolean;
  isModalOpen: boolean;
  editingRole: Role | null;
  formData: RoleCreate;
  currentPage: number;
  pageSize: number;
  totalRoles: number;
};

type Action =
  | { type: 'SET_FIELD'; field: keyof State; value: any }
  | { type: 'OPEN_CREATE' }
  | { type: 'OPEN_EDIT'; role: Role }
  | { type: 'CLOSE_MODAL' }
  | { type: 'TOGGLE_PERMISSION'; permission: string };

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'SET_FIELD':
      return { ...state, [action.field]: action.value };
    case 'OPEN_CREATE':
      return {
        ...state,
        editingRole: null,
        formData: { name: "", description: "", permissions: [] },
        isModalOpen: true,
      };
    case 'OPEN_EDIT':
      return {
        ...state,
        editingRole: action.role,
        formData: {
          name: action.role.name,
          description: action.role.description || "",
          permissions: action.role.permissions,
        },
        isModalOpen: true,
      };
    case 'CLOSE_MODAL':
      return { ...state, isModalOpen: false };
    case 'TOGGLE_PERMISSION': {
      const { permissions } = state.formData;
      const hasPermission = permissions.includes(action.permission);
      const newPermissions = hasPermission
        ? permissions.filter((perm) => perm !== action.permission)
        : [...permissions, action.permission];
      return {
        ...state,
        formData: { ...state.formData, permissions: newPermissions },
      };
    }
    default:
      return state;
  }
}

const initialState: State = {
  roles: [],
  permissionsList: [],
  loading: true,
  isModalOpen: false,
  editingRole: null,
  formData: { name: "", description: "", permissions: [] },
  currentPage: 1,
  pageSize: 20,
  totalRoles: 0,
};

export default function Roles() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const {
    roles,
    permissionsList,
    loading,
    isModalOpen,
    editingRole,
    formData,
    currentPage,
    pageSize,
    totalRoles,
  } = state;

  const fetchData = useCallback(async () => {
    try {
      dispatch({ type: 'SET_FIELD', field: 'loading', value: true });
      const skip = (currentPage - 1) * pageSize;
      const [rolesData, permsData] = await Promise.all([
        rbacService.getRoles({ skip, limit: pageSize }),
        rbacService.getPermissions(),
      ]);

      dispatch({ type: 'SET_FIELD', field: 'roles', value: rolesData });
      dispatch({
        type: 'SET_FIELD',
        field: 'totalRoles',
        value: rolesData.length === pageSize ? currentPage * pageSize + 1 : (currentPage - 1) * pageSize + rolesData.length,
      });
      dispatch({ type: 'SET_FIELD', field: 'permissionsList', value: permsData });
    } catch (error) {
      console.error(error);
      toast.error("Failed to fetch roles");
    } finally {
      dispatch({ type: 'SET_FIELD', field: 'loading', value: false });
    }
  }, [currentPage, pageSize]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const handleSave = async (event: React.FormEvent) => {
    event.preventDefault();
    try {
      if (editingRole) {
        await rbacService.updateRole(editingRole.name, formData);
        toast.success("Role updated");
      } else {
        await rbacService.createRole(formData);
        toast.success("Role created");
      }
      dispatch({ type: 'CLOSE_MODAL' });
      await fetchData();
    } catch (error) {
      const apiError = error as AxiosError<ApiErrorResponse>;
      toast.error(apiError.response?.data?.detail || "Action failed");
    }
  };

  const handleDelete = async (name: string) => {
    if (!confirm(`Are you sure you want to delete role '${name}'?`)) return;
    try {
      await rbacService.deleteRole(name);
      toast.success("Role deleted");
      await fetchData();
    } catch (error) {
      const apiError = error as AxiosError<ApiErrorResponse>;
      toast.error(apiError.response?.data?.detail || "Delete failed");
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-8 w-40 animate-pulse rounded bg-muted" />
        <div className="h-56 animate-pulse rounded-xl border bg-card" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border bg-card p-5 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Roles & Permissions</h1>
            <p className="mt-1 text-sm text-muted-foreground">Define access policies and permission scopes for your organization.</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void fetchData()}
              className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted"
            >
              <RefreshCw className="w-4 h-4" /> Refresh
            </button>
            <button
              type="button"
              onClick={() => dispatch({ type: 'OPEN_CREATE' })}
              className="inline-flex items-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-md text-sm font-medium hover:opacity-90 transition"
            >
              <Plus className="w-4 h-4" /> Create Role
            </button>
          </div>
        </div>
      </div>

      <div className="border rounded-xl bg-card overflow-hidden shadow-sm">
        <table className="w-full text-sm text-left">
          <thead className="bg-muted/50 text-muted-foreground border-b dark:bg-muted/20">
            <tr>
              <th className="px-6 py-3 font-medium">Name</th>
              <th className="px-6 py-3 font-medium">Description</th>
              <th className="px-6 py-3 font-medium">Permissions</th>
              <th className="px-6 py-3 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {roles.map((role) => (
              <tr key={role.name} className="hover:bg-muted/50 dark:hover:bg-muted/10 transition-colors">
                <td className="px-6 py-4 font-medium">
                  <div className="inline-flex items-center gap-2">
                    <ShieldCheck className="w-4 h-4 text-blue-600 dark:text-blue-400" />
                    <span>{role.name}</span>
                  </div>
                </td>
                <td className="px-6 py-4">{role.description || "-"}</td>
                <td className="px-6 py-4">
                  <div className="flex flex-wrap gap-1">
                    {role.permissions.map((permission) => (
                      <span
                        key={permission}
                        className="px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 text-xs border border-blue-200 dark:border-blue-800"
                      >
                        {permission}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="px-6 py-4">
                  <div className="flex items-center justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => dispatch({ type: 'OPEN_EDIT', role })}
                      className="text-muted-foreground hover:text-foreground transition-colors p-1"
                      title="Edit"
                    >
                      <Edit className="w-4 h-4" />
                    </button>
                    {role.name !== "admin" && role.name !== "member" && role.name !== "guest" && (
                      <button
                        type="button"
                        onClick={() => handleDelete(role.name)}
                        className="text-muted-foreground hover:text-red-500 transition-colors p-1"
                        title="Delete"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {roles.length === 0 && (
              <tr>
                <td colSpan={4} className="px-6 py-10 text-center text-muted-foreground">
                  No roles configured.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {roles.length > 0 && (
        <Pagination
          currentPage={currentPage}
          totalPages={Math.ceil(totalRoles / pageSize)}
          onPageChange={(page) => dispatch({ type: 'SET_FIELD', field: 'currentPage', value: page })}
          pageSize={pageSize}
          onPageSizeChange={(size) => {
            dispatch({ type: 'SET_FIELD', field: 'pageSize', value: size });
            dispatch({ type: 'SET_FIELD', field: 'currentPage', value: 1 });
          }}
          totalItems={totalRoles}
        />
      )}

      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-background rounded-lg shadow-lg w-full max-w-lg p-6 space-y-4 border">
            <h3 className="text-xl font-semibold">{editingRole ? "Edit Role" : "Create Role"}</h3>
            <form onSubmit={handleSave} className="space-y-4">
              <div>
                <label htmlFor="role-name" className="block text-sm font-medium mb-1">Role Name</label>
                <input
                  id="role-name"
                  className="w-full p-2 border rounded-md bg-background disabled:opacity-50"
                  value={formData.name}
                  onChange={(event) => dispatch({ type: 'SET_FIELD', field: 'formData', value: { ...formData, name: event.target.value } })}
                  disabled={!!editingRole}
                  required
                />
              </div>
              <div>
                <label htmlFor="role-description" className="block text-sm font-medium mb-1">Description</label>
                <input
                  id="role-description"
                  className="w-full p-2 border rounded-md bg-background"
                  value={formData.description}
                  onChange={(event) => dispatch({ type: 'SET_FIELD', field: 'formData', value: { ...formData, description: event.target.value } })}
                />
              </div>
              <div>
                <span className="block text-sm font-medium mb-2">Permissions</span>
                <div className="grid grid-cols-2 gap-2 h-48 overflow-y-auto border p-2 rounded-md">
                  {permissionsList.map((permission) => (
                    <label key={permission} className="flex items-center space-x-2 text-sm cursor-pointer hover:bg-muted/50 p-1 rounded transition-colors">
                      <input
                        type="checkbox"
                        checked={formData.permissions.includes(permission)}
                        onChange={() => dispatch({ type: 'TOGGLE_PERMISSION', permission })}
                        className="accent-primary"
                      />
                      <span>{permission}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => dispatch({ type: 'CLOSE_MODAL' })}
                  className="px-4 py-2 text-sm font-medium border rounded-md hover:bg-muted"
                >
                  Cancel
                </button>
                <button type="submit" className="px-4 py-2 text-sm font-medium bg-primary text-primary-foreground rounded-md hover:opacity-90">
                  Save
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
