import { spawn } from "node:child_process";
import net from "node:net";
import path from "node:path";

const port = Number(process.env.SALAMANDRA_E2E_PORT || 4173);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid SALAMANDRA_E2E_PORT");

async function listening(): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.connect(port, "127.0.0.1");
    socket.setTimeout(1000);
    socket.once("connect", () => { socket.destroy(); resolve(true); });
    socket.once("error", () => resolve(false));
    socket.once("timeout", () => { socket.destroy(); resolve(false); });
  });
}

export default async function setup() {
  if (await listening()) throw new Error(`QA port ${port} is already occupied; refusing to reuse it.`);
  const python = process.platform === "win32"
    ? path.join(process.cwd(), ".venv", "Scripts", "python.exe") : "python";
  // No shell/process tree: stdin EOF is the cross-platform shutdown protocol.
  const child = spawn(python, ["-u", "tests/e2e_server.py", "--port", String(port), "--parent-stdin"], {
    stdio: ["pipe", "pipe", "pipe"], windowsHide: true,
  });
  let output = "";
  const started = Date.now();
  let lastProbe = "not attempted";
  child.stdout.on("data", (data) => { output = (output + data).slice(-8000); });
  child.stderr.on("data", (data) => { output = (output + data).slice(-8000); });
  let closed = false;
  let spawnError: Error | undefined;
  child.on("error", (error) => { spawnError = error; });
  const exit = new Promise<number | null>((resolve) => {
    child.once("close", (code) => { closed = true; resolve(code); });
  });
  const stop = async () => {
    child.stdin.end();
    let timer: NodeJS.Timeout | undefined;
    try {
      const code = await Promise.race([
        exit,
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => {
            child.kill();
            reject(new Error(`QA fixture did not stop within 10 seconds.\n${output}`));
          }, 10_000);
        }),
      ]);
      if (code !== 0) throw new Error(`QA fixture exited ${code}: ${output}`);
      if (await listening()) throw new Error(`QA fixture left port ${port} listening.`);
      console.log(`QA fixture stopped cleanly; child exited 0; port ${port} closed.`);
    } finally {
      clearTimeout(timer);
    }
  };
  try {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      if (spawnError) throw spawnError;
      if (closed) throw new Error(`QA fixture exited before readiness: ${output}`);
      const response = await fetch(`http://127.0.0.1:${port}/health`, {
        signal: AbortSignal.timeout(1000),
      }).catch((error: Error & { cause?: Error }) => {
        lastProbe = `${error.message}; ${error.cause?.message ?? "no cause"}`;
        return undefined;
      });
      if (response) lastProbe = `HTTP ${response.status}`;
      if (response?.ok) {
        await response.text();
        console.log(`QA fixture ready in ${Date.now() - started}ms; pid ${child.pid}.\n${output}`);
        return stop;
      }
      await response?.body?.cancel();
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error(`QA fixture readiness timed out after ${Date.now() - started}ms; pid ${child.pid}; last probe: ${lastProbe}.\n${output}`);
  } catch (error) {
    try { await stop(); } catch (cleanupError) {
      throw new AggregateError([error, cleanupError], "QA fixture startup and cleanup failed");
    }
    throw error;
  }
}
