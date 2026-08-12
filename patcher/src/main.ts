import { invoke } from "@tauri-apps/api/core";
import "./style.css";

type GameStatus = {
  gameRoot: string;
  gameDataRoot: string;
  addressablesRoot: string;
  gameVersion: string;
  catalogHash: string;
};

type InstalledPatch = {
  patchVersion: string;
  catalogHash: string;
} | null;

type ActionResult = {
  kind: string;
  message: string;
};

const app = document.querySelector<HTMLElement>("#app");
if (!app) throw new Error("#app was not found");

app.innerHTML = `
  <section class="shell">
    <header>
      <div>
        <p class="eyebrow">ASTRAL PARTY</p>
        <h1>한국어 패치</h1>
      </div>
      <span id="connection" class="badge">확인 중</span>
    </header>

    <section class="panel status-grid">
      <div><span>게임 버전</span><strong id="game-version">-</strong></div>
      <div><span>Catalog</span><strong id="catalog-hash">-</strong></div>
      <div><span>설치 패치</span><strong id="patch-version">-</strong></div>
    </section>

    <section class="panel">
      <label for="release-url">Release index URL</label>
      <input id="release-url" type="url" placeholder="https://.../release-index.json" autocomplete="off" />
      <div class="controls">
        <select id="channel">
          <option value="stable">Stable</option>
          <option value="preview">Preview</option>
        </select>
        <button id="install" class="primary">패치 설치 / 업데이트</button>
        <button id="remove">패치 제거</button>
      </div>
    </section>

    <section class="panel details">
      <span>게임 경로</span><code id="game-path">-</code>
      <span>상태</span><p id="message">게임 설치 상태를 확인하고 있습니다.</p>
    </section>
  </section>
`;

const byId = <T extends HTMLElement>(id: string) => {
  const element = document.getElementById(id) as T | null;
  if (!element) throw new Error(`#${id} was not found`);
  return element;
};

const connection = byId<HTMLSpanElement>("connection");
const version = byId<HTMLElement>("game-version");
const catalog = byId<HTMLElement>("catalog-hash");
const patchVersion = byId<HTMLElement>("patch-version");
const gamePath = byId<HTMLElement>("game-path");
const message = byId<HTMLParagraphElement>("message");
const releaseUrl = byId<HTMLInputElement>("release-url");
const channel = byId<HTMLSelectElement>("channel");
const installButton = byId<HTMLButtonElement>("install");
const removeButton = byId<HTMLButtonElement>("remove");

const savedUrl = localStorage.getItem("releaseIndexUrl");
if (savedUrl) releaseUrl.value = savedUrl;

function setBusy(busy: boolean): void {
  installButton.disabled = busy;
  removeButton.disabled = busy;
}

function setMessage(value: string): void {
  message.textContent = value;
}

async function refresh(): Promise<void> {
  try {
    const status = await invoke<GameStatus>("detect_game");
    const installed = await invoke<InstalledPatch>("get_installed_patch");
    connection.textContent = "게임 감지됨";
    connection.dataset.state = "ok";
    version.textContent = status.gameVersion;
    catalog.textContent = status.catalogHash;
    gamePath.textContent = status.gameRoot;
    patchVersion.textContent = installed?.patchVersion ?? "미설치";
    setMessage("패치를 설치할 수 있습니다.");
  } catch (error) {
    connection.textContent = "게임 미감지";
    connection.dataset.state = "error";
    setMessage(String(error));
  }
}

installButton.addEventListener("click", async () => {
  const url = releaseUrl.value.trim();
  if (!url) {
    setMessage("Release index URL을 입력하세요.");
    return;
  }
  localStorage.setItem("releaseIndexUrl", url);
  setBusy(true);
  setMessage("호환 패치를 확인하고 다운로드 중입니다.");
  try {
    const result = await invoke<ActionResult>("install_latest", {
      releaseIndexUrl: url,
      channel: channel.value,
    });
    setMessage(result.message);
    await refresh();
  } catch (error) {
    setMessage(String(error));
  } finally {
    setBusy(false);
  }
});

removeButton.addEventListener("click", async () => {
  setBusy(true);
  setMessage("설치 기록을 검증하고 패치를 제거 중입니다.");
  try {
    const result = await invoke<ActionResult>("remove_installed");
    setMessage(result.message);
    await refresh();
  } catch (error) {
    setMessage(String(error));
  } finally {
    setBusy(false);
  }
});

void refresh();
