import { useEffect, useState } from "react";

export default function NetworkNotice() {
  const [offline, setOffline] = useState(() => !navigator.onLine);
  const [reconnected, setReconnected] = useState(false);
  useEffect(() => {
    const disconnect = () => { setOffline(true); setReconnected(false); };
    const reconnect = () => { setOffline(false); setReconnected(true); };
    window.addEventListener("offline", disconnect);
    window.addEventListener("online", reconnect);
    return () => {
      window.removeEventListener("offline", disconnect);
      window.removeEventListener("online", reconnect);
    };
  }, []);
  if (!offline && !reconnected) return null;
  return <div className={`workspace-network ${offline ? "is-offline" : ""}`} role="status">
    <span>{offline ? "网络已断开，请检查连接。未提交的内容请勿关闭。" : "网络已恢复。可重新检查数据；未完成的操作不会自动提交。"}</span>
    {!offline && <button type="button" className="button secondary" onClick={() => {
      window.dispatchEvent(new Event("focus"));
      setReconnected(false);
    }}>重新检查</button>}
  </div>;
}
