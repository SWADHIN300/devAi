import { DatabaseSync } from "node:sqlite";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { homedir } from "node:os";

const dbPath = process.env.DEVPILOT_DB_PATH || process.env.LOCALAPPDATA ? join(process.env.LOCALAPPDATA, "DevPilot", "devpilot.sqlite3") : join(process.env.XDG_STATE_HOME || join(homedir(), ".local", "state"), "devpilot", "devpilot.sqlite3");
mkdirSync(dirname(dbPath), { recursive: true });
export const db = new DatabaseSync(dbPath);
db.exec(`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS task_runs(id TEXT PRIMARY KEY, task TEXT NOT NULL, workspace_root TEXT NOT NULL, status TEXT NOT NULL, plan TEXT, model TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, approved_at TEXT, rejected_at TEXT);
CREATE INDEX IF NOT EXISTS task_runs_created_idx ON task_runs(created_at DESC);
CREATE TABLE IF NOT EXISTS edit_previews(id TEXT PRIMARY KEY, task_id TEXT REFERENCES task_runs(id) ON DELETE SET NULL, workspace_root TEXT NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL, diff TEXT NOT NULL, expected_sha256 TEXT, created_at TEXT NOT NULL, applied_at TEXT);
CREATE TABLE IF NOT EXISTS tool_events(id TEXT PRIMARY KEY, task_id TEXT REFERENCES task_runs(id) ON DELETE SET NULL, tool_name TEXT NOT NULL, succeeded INTEGER NOT NULL, duration_ms INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS tool_events_task_idx ON tool_events(task_id,created_at);`);
const now = () => new Date().toISOString();
export const store = {
 createRun(task:string, root:string) { const id=randomUUID().replaceAll("-", ""), at=now(); db.prepare("INSERT INTO task_runs(id,task,workspace_root,status,created_at,updated_at) VALUES(?,?,?,'planning',?,?)").run(id,task,root,at,at); return id; },
 complete(id:string,plan:string,model:string) { db.prepare("UPDATE task_runs SET status='pending_approval',plan=?,model=?,updated_at=? WHERE id=? AND status='planning'").run(plan,model,now(),id); },
 fail(id:string,error:string) { db.prepare("UPDATE task_runs SET status='failed',error=?,updated_at=? WHERE id=?").run(error.slice(0,2000),now(),id); },
 get(id:string) { return db.prepare("SELECT * FROM task_runs WHERE id=?").get(id) as Record<string,unknown>|undefined; },
 list(limit:number) { if(limit<1||limit>100) throw Error("limit must be between 1 and 100"); return db.prepare("SELECT * FROM task_runs ORDER BY created_at DESC LIMIT ?").all(limit); },
 decide(id:string, decision:"approved"|"rejected") { const field=decision==="approved"?"approved_at":"rejected_at", at=now(); return db.prepare(`UPDATE task_runs SET status=?,${field}=?,updated_at=? WHERE id=? AND status='pending_approval'`).run(decision,at,at,id).changes===1; },
 event(name:string,ok:boolean,ms:number,taskId?:string) { db.prepare("INSERT INTO tool_events VALUES(?,?,?,?,?,?)").run(randomUUID(),taskId||null,name,ok?1:0,Math.max(0,ms),now()); },
 events(id:string) { return db.prepare("SELECT id,tool_name,succeeded,duration_ms,created_at FROM tool_events WHERE task_id=? ORDER BY created_at LIMIT 100").all(id); }
};
