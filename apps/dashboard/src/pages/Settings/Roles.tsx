import { useCallback, useEffect, useState } from "react";
import { rbacService } from "@/services/rbacService";
import type { Role, RoleCreate } from "@/services/rbacService";
import { toast } from "sonner";
import { Plus, Trash2, Edit, ShieldCheck } from "lucide-react";
import { Pagination } from "@/components/ui/Pagination";
import type { AxiosError } from "axios";

type ApiErrorResponse = {
  detail?: string;
};

export default function Roles() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [permissionsList, setPermissionsList] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);

  const [editingRole, setEditingRole] = useState<Role | null>(null);
  const [formData, setFormData] = useState<RoleCreate>({ name: "", description: "", permissions: [] });

  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [totalRoles, setTotalRoles] = useState(0);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const skip = (currentPage - 1) * pageSize;
      const [rolesData, permsData] = await Promise.all([
        rbacService.getRoles({ skip, limit: pageSize }),
        rbacService.getPermissions(),
      ]);

      setRoles(rolesData);
      setTotalRoles(rolesData.length === pageSize ? currentPage * pageSize + 1 : (currentPage - 1) * pageSize + rolesData.length);
      setPermissionsList(permsData);
    } catch (error) {
      console.error(error);
      toast.error("Failed to fetch roles");
    } finally {
      setLoading(false);
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
      setIsModalOpen(false);
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

  const openCreate = () => {
    setEditingRole(null);
    setFormData({ name: "", description: "", permissions: [] });
    setIsModalOpen(true);
  };

  const openEdit = (role: Role) => {
    setEditingRole(role);
    setFormData({
      name: role.name,
      description: role.description || "",
      permissions: role.permissions,
    });
    setIsModalOpen(true);
  };

  const togglePermission = (permission: string) => {
    setFormData((prev) => {
      const hasPermission = prev.permissions.includes(permission);
      if (hasPermission) {
        return { ...prev, permissions: prev.permissions.filter((perm) => perm !== permission) };
      }
      return { ...prev, permissions: [...prev.permissions, permission] };
    });
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
          <button
            type="button"
            onClick={openCreate}
            className="inline-flex items-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-md text-sm font-medium hover:opacity-90 transition"
          >
            <Plus className="w-4 h-4" /> Create Role
          </button>
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
                      onClick={() => openEdit(role)}
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
          onPageChange={setCurrentPage}
          pageSize={pageSize}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setCurrentPage(1);
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
                <label className="block text-sm font-medium mb-1">Role Name</label>
                <input
                  className="w-full p-2 border rounded-md bg-background disabled:opacity-50"
                  value={formData.name}
                  onChange={(event) => setFormData({ ...formData, name: event.target.value })}
                  disabled={!!editingRole}
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Description</label>
                <input
                  className="w-full p-2 border rounded-md bg-background"
                  value={formData.description}
                  onChange={(event) => setFormData({ ...formData, description: event.target.value })}
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-2">Permissions</label>
                <div className="grid grid-cols-2 gap-2 h-48 overflow-y-auto border p-2 rounded-md">
                  {permissionsList.map((permission) => (
                    <label key={permission} className="flex items-center space-x-2 text-sm">
                      <input
                        type="checkbox"
                        checked={formData.permissions.includes(permission)}
                        onChange={() => togglePermission(permission)}
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
                  onClick={() => setIsModalOpen(false)}
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
