/**
 * Administrator management — Super Admin only.
 *
 * The permission vocabulary is fetched rather than hardcoded: it lives in
 * app/core/permissions.py, and a capability added there must appear here
 * without a frontend release. A duplicated list is exactly the drift this
 * whole phase exists to remove.
 *
 * Every permission is its own toggle, per the requirement that each is
 * individually configurable — no bundles, no preset roles.
 */
import { Check, Shield, Trash2, UserPlus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { adminApi, ApiError } from "../lib/api";
import type { AdminAccount } from "../types/admin";
import { Button, EmptyState, Notice, SectionTitle, TextInput } from "./ui";

export function AdminsPanel() {
  const [admins, setAdmins] = useState<AdminAccount[]>([]);
  const [groups, setGroups] = useState<Record<string, string[]>>({});
  const [newTelegramId, setNewTelegramId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const [list, catalog] = await Promise.all([
        adminApi.listAdmins(),
        adminApi.permissionCatalog(),
      ]);
      setAdmins(list);
      setGroups(catalog.groups);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Yuklab bo'lmadi.");
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const guard = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      await reload();
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Amal bajarilmadi.");
    } finally {
      setBusy(false);
    }
  };

  const addAdmin = () => {
    const id = Number(newTelegramId.trim());
    if (!Number.isFinite(id) || id <= 0) {
      setError("Telegram ID noto'g'ri.");
      return;
    }
    void guard(async () => {
      await adminApi.createAdmin(id, []);
      setNewTelegramId("");
    });
  };

  const togglePermission = (admin: AdminAccount, permission: string) => {
    const next = admin.permissions.includes(permission)
      ? admin.permissions.filter((p) => p !== permission)
      : [...admin.permissions, permission];
    void guard(() => adminApi.setAdminPermissions(admin.id, next));
  };

  return (
    <div className="space-y-4">
      <SectionTitle>Adminlar</SectionTitle>
      {error && <Notice message={error} tone="error" />}

      <div className="flex items-end gap-2">
        <div className="flex-1">
          <TextInput
            value={newTelegramId}
            onChange={setNewTelegramId}
            placeholder="Telegram ID"
            mono
          />
        </div>
        <Button onClick={addAdmin} disabled={busy}>
          <UserPlus size={14} /> Qo'shish
        </Button>
      </div>
      <p className="font-body text-xs text-ink-dim">
        Foydalanuvchi avval botga /start yuborgan bo'lishi kerak.
      </p>

      {admins.length === 0 ? (
        <EmptyState message="Admin yo'q." />
      ) : (
        <ul className="space-y-3">
          {admins.map((admin) => (
            <li
              key={admin.id}
              className="rounded-xl border border-surface-hi bg-surface p-3"
            >
              <div className="mb-2 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="flex items-center gap-1.5 truncate font-body text-sm text-ink">
                    {admin.is_super_admin && (
                      <Shield size={13} className="shrink-0 text-marquee" />
                    )}
                    {admin.full_name ?? admin.username ?? admin.telegram_id}
                  </p>
                  <p className="font-mono text-[11px] text-ink-dim">{admin.telegram_id}</p>
                </div>
                {/* The Super Admin has no per-permission rows and cannot be
                    removed here — ownership transfers via configuration. */}
                {!admin.is_super_admin && (
                  <Button
                    tone="danger"
                    disabled={busy}
                    onClick={() => void guard(() => adminApi.removeAdmin(admin.id))}
                  >
                    <Trash2 size={13} />
                  </Button>
                )}
              </div>

              {admin.is_super_admin ? (
                <p className="font-body text-xs text-ink-dim">
                  Super Admin — barcha ruxsatlar doimiy.
                </p>
              ) : (
                <div className="space-y-2">
                  {Object.entries(groups).map(([group, permissions]) => (
                    <div key={group}>
                      <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-ink-dim">
                        {group}
                      </p>
                      <div className="flex flex-wrap gap-1.5">
                        {permissions.map((permission) => {
                          const active = admin.permissions.includes(permission);
                          return (
                            <button
                              key={permission}
                              disabled={busy}
                              onClick={() => togglePermission(admin, permission)}
                              className={`flex items-center gap-1 rounded-full px-2.5 py-1 font-body text-[11px] transition-colors disabled:opacity-50 ${
                                active
                                  ? "bg-marquee text-on-marquee"
                                  : "border border-surface-hi bg-bg text-ink-dim"
                              }`}
                            >
                              {active && <Check size={10} />}
                              {permission}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
