import { useEffect, useState } from "react";
import { rbacService } from "@/services/rbacService";
import type { Role, RoleCreate } from "@/services/rbacService";
import { toast } from "sonner";
import { Plus, Trash2, Edit, Shield } from "lucide-react";
import { Pagination } from "@/components/ui/Pagination";

export default function Roles() {
    const [roles, setRoles] = useState<Role[]>([]);
    const [permissionsList, setPermissionsList] = useState<string[]>([]);
    const [loading, setLoading] = useState(true);
    const [isModalOpen, setIsModalOpen] = useState(false);

    // Form State
    const [editingRole, setEditingRole] = useState<Role | null>(null);
    const [formData, setFormData] = useState<RoleCreate>({ name: "", description: "", permissions: [] });

    // Pagination
    const [currentPage, setCurrentPage] = useState(1);
    const [pageSize, setPageSize] = useState(20);
    const [totalRoles, setTotalRoles] = useState(0);

    useEffect(() => {
        fetchData();
    }, [currentPage, pageSize]);

    const fetchData = async () => {
        try {
            setLoading(true);
            const skip = (currentPage - 1) * pageSize;
            const [rolesData, permsData] = await Promise.all([
                rbacService.getRoles({ skip, limit: pageSize }),
                rbacService.getPermissions()
            ]);
            setRoles(rolesData);
            setTotalRoles(rolesData.length === pageSize ? (currentPage * pageSize) + 1 : (currentPage - 1) * pageSize + rolesData.length);
            setPermissionsList(permsData);
        } catch (error) {
            console.error(error);
            toast.error("Failed to fetch roles");
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async (e: React.FormEvent) => {
        e.preventDefault();
        try {
            if (editingRole) {
                await rbacService.updateRole(editingRole.name, formData);
                toast.success("Role updated");
            } else {
                await rbacService.createRole(formData);
                toast.success("Role created");
            }
            setIsModalOpen(false);
            fetchData();
        } catch (error: any) {
            toast.error(error.response?.data?.detail || "Action failed");
        }
    };

    const handleDelete = async (name: string) => {
        if (!confirm(`Are you sure you want to delete role '${name}'?`)) return;
        try {
            await rbacService.deleteRole(name);
            toast.success("Role deleted");
            fetchData();
        } catch (error: any) {
            toast.error(error.response?.data?.detail || "Delete failed");
        }
    };

    const openCreate = () => {
        setEditingRole(null);
        setFormData({ name: "", description: "", permissions: [] });
        setIsModalOpen(true);
    };

    const openEdit = (role: Role) => {
        if (role.name === "admin" || role.name === "member") {
            // Maybe allow editing description/perms but not name? Backend handles name immutability.
        }
        setEditingRole(role);
        setFormData({
            name: role.name,
            description: role.description || "",
            permissions: role.permissions
        });
        setIsModalOpen(true);
    };

    const togglePermission = (perm: string) => {
        setFormData(prev => {
            const has = prev.permissions.includes(perm);
            if (has) return { ...prev, permissions: prev.permissions.filter(p => p !== perm) };
            return { ...prev, permissions: [...prev.permissions, perm] };
        });
    };

    if (loading) return <div>Loading roles...</div>;

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-center">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">Roles</h2>
                    <p className="text-muted-foreground">Manage roles and permissions.</p>
                </div>
                <button
                    onClick={openCreate}
                    className="flex items-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-md hover:opacity-90 transition"
                >
                    <Plus className="w-4 h-4" />
                    Create Role
                </button>
            </div>

            <div className="border rounded-lg bg-card overflow-hidden">
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
                        {roles.map(role => (
                            <tr key={role.name} className="hover:bg-muted/50 dark:hover:bg-muted/10 transition-colors">
                                <td className="px-6 py-4 font-medium">{role.name}</td>
                                <td className="px-6 py-4">{role.description || "-"}</td>
                                <td className="px-6 py-4">
                                    <div className="flex flex-wrap gap-1">
                                        {role.permissions.map(p => (
                                            <span key={p} className="px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 text-xs border border-blue-200 dark:border-blue-800">
                                                {p}
                                            </span>
                                        ))}
                                    </div>
                                </td>
                                <td className="px-6 py-4 text-right space-x-2">
                                    <button
                                        onClick={() => openEdit(role)}
                                        className="text-muted-foreground hover:text-foreground transition-colors p-1"
                                        title="Edit"
                                    >
                                        <Edit className="w-4 h-4" />
                                    </button>
                                    {/* Protect System Roles slightly */}
                                    {role.name !== "admin" && role.name !== "member" && role.name !== "guest" && (
                                        <button
                                            onClick={() => handleDelete(role.name)}
                                            className="text-muted-foreground hover:text-red-500 transition-colors p-1"
                                            title="Delete"
                                        >
                                            <Trash2 className="w-4 h-4" />
                                        </button>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {/* Pagination */}
            {!loading && roles.length > 0 && (
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

            {/* Modal */}
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
                                    onChange={e => setFormData({ ...formData, name: e.target.value })}
                                    disabled={!!editingRole} // PK cannot change
                                    required
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium mb-1">Description</label>
                                <input
                                    className="w-full p-2 border rounded-md bg-background"
                                    value={formData.description}
                                    onChange={e => setFormData({ ...formData, description: e.target.value })}
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium mb-2">Permissions</label>
                                <div className="grid grid-cols-2 gap-2 h-48 overflow-y-auto border p-2 rounded-md">
                                    {permissionsList.map(perm => (
                                        <label key={perm} className="flex items-center space-x-2 text-sm">
                                            <input
                                                type="checkbox"
                                                checked={formData.permissions.includes(perm)}
                                                onChange={() => togglePermission(perm)}
                                                className="accent-primary"
                                            />
                                            <span>{perm}</span>
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
                                <button
                                    type="submit"
                                    className="px-4 py-2 text-sm font-medium bg-primary text-primary-foreground rounded-md hover:opacity-90"
                                >
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
