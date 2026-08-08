/**
 * Paginated user list with balance, premium status and the ban control.
 *
 * Banning is confirmed before it applies: it cuts the user off from the
 * bot and the Mini App at once, and the row it acts on is one line in a
 * list of twenty. The backend refuses the cases that would damage the
 * authorization model — the Super Admin, another administrator when the
 * actor is not the Super Admin, and yourself — so a refusal here is
 * surfaced rather than second-guessed.
 */
import { useCallback, useEffect, useState } from "react";
import { adminApi, ApiError } from "../lib/api";
import type { AdminUser } from "../types/admin";
import { Badge, Button, EmptyState, Notice, SectionTitle, TextInput, formatMoney } from "./ui";

const PAGE_SIZE = 20;

export function UsersPanel() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState("");
  const [confirming, setConfirming] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const result = await adminApi.listUsers({
        q: query || undefined,
        page,
        page_size: PAGE_SIZE,
      });
      setUsers(result.items);
      setTotal(result.total);
    } catch {
      setUsers([]);
      setTotal(0);
    }
  }, [query, page]);

  useEffect(() => {
    load();
  }, [load]);

  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  const setBan = async (user: AdminUser, banned: boolean) => {
    try {
      const updated = await adminApi.setUserBan(user.id, banned);
      // Patches the row in place rather than reloading the page: a reload
      // re-sorts and the admin loses the row they were looking at.
      setUsers((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Amalni bajarib bo'lmadi.");
    } finally {
      setConfirming(null);
    }
  };

  return (
    <div className="space-y-3">
      <SectionTitle>Foydalanuvchilar</SectionTitle>
      {error && <Notice message={error} tone="error" />}

      <TextInput
        value={query}
        onChange={(value) => {
          setPage(0);
          setQuery(value);
        }}
        placeholder="Username bo'yicha qidirish…"
      />

      <p className="font-mono text-[11px] text-ink-dim">{total} ta foydalanuvchi</p>

      {users.length === 0 ? (
        <EmptyState message="Foydalanuvchi topilmadi." />
      ) : (
        <ul className="space-y-2">
          {users.map((user) => (
            <li key={user.id} className="rounded-xl border border-surface-hi bg-surface p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate font-body text-sm text-ink">
                    {user.username ? `@${user.username}` : (user.full_name ?? "—")}
                  </p>
                  <p className="font-mono text-[11px] text-ink-dim">{user.telegram_id}</p>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  {user.is_banned && <Badge>Bloklangan</Badge>}
                  {user.is_premium && <Badge active>Premium</Badge>}
                  <span className="font-mono text-sm text-marquee">{formatMoney(user.balance)}</span>
                </div>
              </div>

              {confirming === user.id ? (
                <div className="mt-2 space-y-2">
                  <p className="font-body text-xs text-ink-dim">
                    {user.is_banned
                      ? "Blokdan chiqarilsinmi?"
                      : "Bloklansinmi? Foydalanuvchi bot va ilovadan foydalana olmaydi."}
                  </p>
                  <div className="grid grid-cols-2 gap-2">
                    <Button
                      tone={user.is_banned ? "primary" : "danger"}
                      onClick={() => setBan(user, !user.is_banned)}
                    >
                      {user.is_banned ? "Blokdan chiqarish" : "Bloklash"}
                    </Button>
                    <Button tone="ghost" onClick={() => setConfirming(null)}>
                      Bekor qilish
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="mt-2">
                  <Button full tone="ghost" onClick={() => setConfirming(user.id)}>
                    {user.is_banned ? "Blokdan chiqarish" : "Bloklash"}
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {lastPage > 0 && (
        <div className="flex items-center justify-between pt-2">
          <Button tone="ghost" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
            Oldingi
          </Button>
          <span className="font-mono text-xs text-ink-dim">
            {page + 1} / {lastPage + 1}
          </span>
          <Button tone="ghost" disabled={page >= lastPage} onClick={() => setPage((p) => p + 1)}>
            Keyingi
          </Button>
        </div>
      )}
    </div>
  );
}
