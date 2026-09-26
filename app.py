
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading, queue, os, sys, json, traceback, subprocess, tempfile, urllib.request, hashlib, io
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta

APP_VERSION = "3.4.3"
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/akramwasimjmi1994-wq/NSE-Bullish-Scanner/main/update.json"
APP_NAME = "NSE_Bullish_Scanner.exe"

DATA_DIR = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "NSEBullishScanner"
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def log(msg):
    try:
        with open(LOG_DIR / "app.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass

def fatal(exc):
    log("FATAL\n" + traceback.format_exc())
    try:
        messagebox.showerror("NSE Bullish Scanner", f"The application could not start.\n\n{exc}\n\nLog:\n{LOG_DIR / 'app.log'}")
    except Exception:
        pass

try:
    import numpy as np
    import pandas as pd
    import yfinance as yf
    from signal_engine_v2 import MarketRegime, evaluate_signal, get_market_regime, passes_liquidity_filter
except Exception as e:
    fatal(e)
    raise

# Current NSE equity universe, refreshed daily and cached locally.
NSE_SYMBOL_CACHE = DATA_DIR / "nse_symbols.json"

def _download_nse(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149 Safari/537.36",
        "Accept": "text/csv,application/json,text/plain,*/*",
        "Referer": "https://www.nseindia.com/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def get_nse_symbols(force=False):
    if not force and NSE_SYMBOL_CACHE.exists():
        try:
            obj=json.loads(NSE_SYMBOL_CACHE.read_text(encoding="utf-8"))
            if datetime.now().timestamp()-float(obj.get("timestamp",0)) < 86400 and len(obj.get("symbols",[])) >= 500:
                return list(obj["symbols"])
        except Exception:
            pass
    last_err=None
    for url in ("https://archives.nseindia.com/content/equities/EQUITY_L.csv",
                "https://www.nseindia.com/api/equity-master"):
        try:
            raw=_download_nse(url)
            df=pd.read_csv(io.StringIO(raw.decode("utf-8-sig",errors="replace")))
            cols={str(x).strip().upper():x for x in df.columns}
            sym_col=cols.get("SYMBOL")
            series_col=cols.get("SERIES")
            if not sym_col: raise ValueError("NSE equity list has no SYMBOL column")
            if series_col:
                df=df[df[series_col].astype(str).str.upper().isin({"EQ","BE","BZ"})]
            syms=sorted({str(x).strip().upper() for x in df[sym_col].dropna() if str(x).strip()})
            if len(syms)<500: raise ValueError(f"NSE returned only {len(syms)} symbols")
            NSE_SYMBOL_CACHE.write_text(json.dumps({"timestamp":datetime.now().timestamp(),"symbols":syms}),encoding="utf-8")
            return syms
        except Exception as e:
            last_err=e
            log(f"NSE list failed: {e}")
    if NSE_SYMBOL_CACHE.exists():
        try:
            obj=json.loads(NSE_SYMBOL_CACHE.read_text(encoding="utf-8"))
            if len(obj.get("symbols",[]))>=500: return list(obj["symbols"])
        except Exception: pass
    raise RuntimeError(f"Could not load NSE equity universe: {last_err}")

INDEX_SYMBOL_CACHE = DATA_DIR / "index_symbols.json"
INDEX_URLS = {
    "Nifty 50": "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "Nifty 100": "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv",
    "Nifty 200": "https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv",
}
def get_index_symbols(index_name, force=False):
    if index_name == "All NSE": return get_nse_symbols(force=force)
    key=index_name.replace(" ","_").lower()
    cached=json.loads(INDEX_SYMBOL_CACHE.read_text(encoding="utf-8")) if INDEX_SYMBOL_CACHE.exists() else {}
    item=cached.get(key,{})
    if not force and datetime.now().timestamp()-float(item.get("timestamp",0)) < 86400 and len(item.get("symbols",[])) >= 20:
        return list(item["symbols"])
    try:
        raw=_download_nse(INDEX_URLS[index_name])
        df=pd.read_csv(io.StringIO(raw.decode("utf-8-sig",errors="replace")))
        cols={str(x).strip().upper():x for x in df.columns}; sym_col=cols.get("SYMBOL")
        if not sym_col: raise ValueError("No SYMBOL column")
        syms=sorted({str(x).strip().upper() for x in df[sym_col].dropna() if str(x).strip() and str(x).strip().upper()!=index_name.upper()})
        if len(syms)<20: raise ValueError(f"Only {len(syms)} symbols returned")
        cached[key]={"timestamp":datetime.now().timestamp(),"symbols":syms}
        INDEX_SYMBOL_CACHE.write_text(json.dumps(cached),encoding="utf-8")
        return syms
    except Exception as e:
        log(f"{index_name} list failed: {e}")
        if len(item.get("symbols",[])) >= 20: return list(item["symbols"])
        raise RuntimeError(f"Could not load {index_name}: {e}")

def ema(s,n): return s.ewm(span=n,adjust=False).mean()
def rma(s,n): return s.ewm(alpha=1/n,adjust=False).mean()

def rsi(c,n=14):
    d=c.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    rs=rma(up,n)/rma(dn,n).replace(0,np.nan)
    return 100-100/(1+rs)

def atr(df,n=14):
    p=df.Close.shift(1)
    tr=pd.concat([(df.High-df.Low),(df.High-p).abs(),(df.Low-p).abs()],axis=1).max(axis=1)
    return rma(tr,n)

def adx(df,n=14):
    up=df.High.diff(); dn=-df.Low.diff()
    plus=pd.Series(np.where((up>dn)&(up>0),up,0),index=df.index)
    minus=pd.Series(np.where((dn>up)&(dn>0),dn,0),index=df.index)
    a=atr(df,n)
    pdi=100*rma(plus,n)/a.replace(0,np.nan)
    mdi=100*rma(minus,n)/a.replace(0,np.nan)
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    return rma(dx,n)

def vwap(df):
    tp=(df.High+df.Low+df.Close)/3
    vol=df.Volume.fillna(0)
    if isinstance(df.index,pd.DatetimeIndex) and len(df):
        dates=df.index.date
        pv=pd.Series(tp*vol,index=df.index).groupby(dates).cumsum()
        vv=pd.Series(vol,index=df.index).groupby(dates).cumsum()
        return pv/vv.replace(0,np.nan)
    return (tp*vol).cumsum()/vol.cumsum().replace(0,np.nan)

def supertrend(df,period=10,mult=3):
    a=atr(df,period); mid=(df.High+df.Low)/2
    up=mid+mult*a; lo=mid-mult*a
    fu=up.copy(); fl=lo.copy()
    direction=pd.Series(1,index=df.index,dtype=int)
    st=pd.Series(np.nan,index=df.index)
    if len(df): st.iloc[0]=lo.iloc[0]
    for i in range(1,len(df)):
        fu.iloc[i]=up.iloc[i] if up.iloc[i]<fu.iloc[i-1] or df.Close.iloc[i-1]>fu.iloc[i-1] else fu.iloc[i-1]
        fl.iloc[i]=lo.iloc[i] if lo.iloc[i]>fl.iloc[i-1] or df.Close.iloc[i-1]<fl.iloc[i-1] else fl.iloc[i-1]
        if st.iloc[i-1]==fu.iloc[i-1]:
            direction.iloc[i]=-1 if df.Close.iloc[i]<=fu.iloc[i] else 1
        else:
            direction.iloc[i]=1 if df.Close.iloc[i]>=fl.iloc[i] else -1
        st.iloc[i]=fl.iloc[i] if direction.iloc[i]==1 else fu.iloc[i]
    return st,direction

def calc(df):
    d=df.copy()
    d["EMA20"]=ema(d.Close,20); d["EMA50"]=ema(d.Close,50)
    d["VWAP"]=vwap(d); d["RSI14"]=rsi(d.Close)
    d["ADX14"]=adx(d); d["RelVol"]=d.Volume/d.Volume.rolling(20).mean()
    d["Supertrend"],d["STDir"]=supertrend(d)
    d["MACD"]=ema(d.Close,12)-ema(d.Close,26)
    d["MACDSignal"]=ema(d["MACD"],9)
    return d

def liquidity_metrics(d):
    if d is None or d.empty:
        return 0.0, 0.0
    recent=d.tail(20).copy()
    return float(recent["Volume"].mean()), float((recent["Close"]*recent["Volume"]).mean())

def historical_market_regime(index_df, timestamp):
    if index_df is None or index_df.empty:
        return MarketRegime(True,0,0,"Index data unavailable — regime filter skipped")
    ts=pd.Timestamp(timestamp)
    if getattr(ts,"tzinfo",None) is not None:
        ts=ts.tz_localize(None)
    idx=index_df
    if getattr(idx.index,"tz",None) is not None:
        idx=idx.copy()
        idx.index=idx.index.tz_localize(None)
    prior=idx.loc[idx.index<=ts]
    if len(prior)<55:
        return MarketRegime(True,0,0,"Insufficient historical NIFTY data — regime filter skipped")
    x=prior.iloc[-1]
    price=float(x["Close"]); ema50=float(x["EMA50"])
    return MarketRegime(price>ema50,price,ema50,"NIFTY above 50-EMA" if price>ema50 else "NIFTY below 50-EMA")

def fetch_historical_market_regime(start,end):
    try:
        data=yf.Ticker("^NSEI").history(start=start,end=end,interval="1d",auto_adjust=False,prepost=False)
        if data is None or data.empty:
            return pd.DataFrame()
        data=data.copy()
        data["EMA50"]=data["Close"].ewm(span=50,adjust=False).mean()
        return data[["Close","EMA50"]].dropna()
    except Exception as e:
        log(f"historical regime fetch failed: {e}")
        return pd.DataFrame()

def fetch(sym,interval,period=None,start=None,end=None):
    try:
        t=yf.Ticker(sym+".NS")
        if start: d=t.history(start=start,end=end,interval=interval,auto_adjust=False,prepost=False)
        else: d=t.history(period=period,interval=interval,auto_adjust=False,prepost=False)
        if d is None or d.empty: return pd.DataFrame()
        if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(-1)
        cols=["Open","High","Low","Close","Volume"]
        return d[cols].dropna() if all(x in d.columns for x in cols) else pd.DataFrame()
    except Exception as e:
        log(f"fetch {sym}: {e}"); return pd.DataFrame()

def fetch_batch(symbols,interval,period):
    if not symbols: return {}
    try:
        raw=yf.download(tickers=[s+".NS" for s in symbols],interval=interval,period=period,
                        auto_adjust=False,prepost=False,group_by="ticker",threads=True,progress=False,timeout=20)
    except Exception as e:
        log(f"batch download failed ({len(symbols)}): {e}"); return {}
    out={}
    if raw is None or raw.empty: return out
    for sym in symbols:
        ticker=sym+".NS"
        try:
            if isinstance(raw.columns,pd.MultiIndex):
                if ticker in raw.columns.get_level_values(0): d=raw[ticker].copy()
                elif ticker in raw.columns.get_level_values(1): d=raw.xs(ticker,axis=1,level=1).copy()
                else: continue
            else:
                if len(symbols)!=1: continue
                d=raw.copy()
            cols=["Open","High","Low","Close","Volume"]
            if all(x in d.columns for x in cols):
                d=d[cols].dropna()
                if not d.empty: out[sym]=d
        except Exception as e: log(f"extract {sym}: {e}")
    return out

def build_signal_snapshot(sym,d,regime,daily_d=None):
    if d is None or len(d)<60: return None
    try:
        d=calc(d); x=d.iloc[-1]
        avg_volume,avg_value=liquidity_metrics(daily_d) if daily_d is not None and not daily_d.empty else (0,0)
        liquid=passes_liquidity_filter(avg_volume,avg_value)
        signal=evaluate_signal(
            price=float(x.Close), ema20=float(x.EMA20), ema50=float(x.EMA50),
            vwap=float(x.VWAP), rsi14=float(x.RSI14), adx14=float(x.ADX14),
            relative_volume=float(x.RelVol), supertrend_bullish=bool(x.STDir==1),
            macd=float(x.MACD), macd_signal=float(x.MACDSignal), regime=regime,
        )
        sc=signal.composite_score
        qualified=bool(signal.qualifies and liquid)
        status="BUY" if qualified else ("WATCH" if liquid else "LOW LIQUIDITY")
        row=(sym,f"{x.Close:.2f}",f"{sc}/100",f"{x.RSI14:.1f}",f"{x.ADX14:.1f}",f"{x.RelVol:.2f}",
             f"{x.EMA20:.2f}",f"{x.EMA50:.2f}",f"{x.VWAP:.2f}",
             "BULLISH" if x.STDir==1 else "BEARISH",f"{x.MACD:.3f}",status)
        return {
            "symbol":sym,"data":d,"latest":x,"signal":signal,"score":sc,
            "liquid":liquid,"avg_volume":avg_volume,"avg_value":avg_value,
            "status":status,"row":row
        }
    except Exception as e:
        log(f"signal snapshot {sym}: {e}"); return None

def scan_one(sym,d,regime,daily_d=None):
    snap=build_signal_snapshot(sym,d,regime,daily_d)
    if not snap: return None
    return snap["signal"],snap["row"]


def trade_setup(sym, interval):
    settings = {"15 min":("15m","60d"),"1 hour":("60m","730d"),"1 day":("1d","10y")}
    yf_interval, period = settings[interval]
    d = fetch(sym.upper().strip(), yf_interval, period=period)
    if d.empty or len(d) < 60:
        raise ValueError("Not enough market data available for this stock/timeframe.")
    daily = fetch(sym.upper().strip(),"1d",period="60d")
    snap = build_signal_snapshot(sym.upper().strip(), d, get_market_regime(), daily)
    if not snap:
        raise ValueError("Could not calculate a complete signal for this stock/timeframe.")
    cd=snap["data"]; x=snap["latest"]; score=snap["score"]
    close=float(x.Close); atrv=float(atr(cd,14).iloc[-1])
    entry=close; stop=max(0.01,entry-1.5*atrv); target=entry+3.0*atrv
    risk=entry-stop; reward=target-entry; breakout=float(cd.High.tail(20).max())
    return {
        "symbol":sym.upper().strip(),"price":close,"entry":entry,"stop":stop,"target":target,
        "risk":risk,"reward":reward,"rr":reward/risk if risk else 0,"atr":atrv,"score":score,
        "rsi":float(x.RSI14),"adx":float(x.ADX14),"relvol":float(x.RelVol),
        "ema20":float(x.EMA20),"ema50":float(x.EMA50),"vwap":float(x.VWAP),
        "macd":float(x.MACD),"macd_signal":float(x.MACDSignal),
        "supertrend":"BULLISH" if x.STDir==1 else "BEARISH","breakout":breakout,
        "time":str(cd.index[-1]),"liquid":snap["liquid"],
        "signal":"BUY" if snap["signal"].qualifies and snap["liquid"] else "WATCH"
    }

def dashboard_snapshot(sym, interval):
    """Build a paper-trading dashboard snapshot from the latest Yahoo Finance bars."""
    period = {"15 min":"60d","1 hour":"730d","1 day":"1y"}[interval]
    yf_interval = {"15 min":"15m","1 hour":"60m","1 day":"1d"}[interval]
    d = fetch(sym, yf_interval, period=period)
    if d.empty or len(d) < 60: return None
    d = calc(d); x = d.iloc[-1]
    regime=get_market_regime()
    signal=evaluate_signal(
        price=float(x.Close), ema20=float(x.EMA20), ema50=float(x.EMA50),
        vwap=float(x.VWAP), rsi14=float(x.RSI14), adx14=float(x.ADX14),
        relative_volume=float(x.RelVol), supertrend_bullish=bool(x.STDir==1),
        macd=float(x.MACD), macd_signal=float(x.MACDSignal), regime=regime,
    )
    score=signal.composite_score
    bullish=signal.qualifies
    entry=float(x.Close); atrv=float(atr(d,14).iloc[-1])
    return {"symbol":sym,"price":entry,"score":score,
            "signal":"BUY" if score>=6 and bullish else ("EXIT" if score<=3 or not bullish else "WATCH"),
            "rsi":float(x.RSI14),"adx":float(x.ADX14),"relvol":float(x.RelVol),
            "st":"BULLISH" if bullish else "BEARISH","entry":entry,
            "stop":max(.01,entry-1.5*atrv),"target":entry+3*atrv,"time":str(d.index[-1])}

def check_update():
    with urllib.request.urlopen(UPDATE_MANIFEST_URL,timeout=10) as r:
        info=json.loads(r.read().decode())
    latest=str(info.get("version",APP_VERSION))
    def pv(v): return tuple(int(x) for x in v.split(".")[:3])
    if pv(latest)>pv(APP_VERSION): return info
    return None

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"NSE Bullish Scanner v{APP_VERSION}")
        self.geometry("1500x900")
        self.minsize(1200,700)
        self.protocol("WM_DELETE_WINDOW",self.destroy)
        self.q=queue.Queue(); self.stop_flag=False; self.trades=[]; self.dash_running=False; self.scan_started=None
        self.setup_styles()
        self.make_ui(); self.after(200,self.poll)
        log("Application started.")


    def setup_styles(self):
        style=ttk.Style(self)
        try: style.theme_use("clam")
        except Exception: pass
        self.configure(bg="#0d1117")
        style.configure(".",font=("Segoe UI",10),background="#0d1117",foreground="#d1d4dc")
        style.configure("TFrame",background="#0d1117")
        style.configure("Header.TFrame",background="#131722")
        style.configure("TButton",background="#1e222d",foreground="#d1d4dc",padding=(12,8),borderwidth=0)
        style.map("TButton",background=[("active","#2a2e39")])
        style.configure("Nav.TButton",background="#131722",foreground="#787b86",padding=(16,10),font=("Segoe UI Semibold",10),borderwidth=0)
        style.configure("Nav.Active.TButton",background="#1e222d",foreground="#26a69a",padding=(16,10),font=("Segoe UI Semibold",10),borderwidth=0)
        style.map("Nav.TButton",background=[("active","#1e222d")],foreground=[("active","#f5f7fa")])
        style.configure("Accent.TButton",background="#26a69a",foreground="#ffffff",padding=(14,9),font=("Segoe UI Semibold",10),borderwidth=0)
        style.map("Accent.TButton",background=[("active","#2bbbad")])
        style.configure("Danger.TButton",background="#ef5350",foreground="#ffffff",padding=(12,8),borderwidth=0)
        style.map("Danger.TButton",background=[("active","#ff625f")])
        style.configure("TCombobox",fieldbackground="#1e222d",background="#1e222d",foreground="#d1d4dc")
        style.configure("TEntry",fieldbackground="#1e222d",foreground="#d1d4dc",insertcolor="#ffffff")
        style.configure("TSpinbox",fieldbackground="#1e222d",foreground="#d1d4dc")
        style.configure("Treeview",background="#131722",fieldbackground="#131722",foreground="#d1d4dc",rowheight=42,borderwidth=0,font=("Segoe UI",10))
        style.configure("Treeview.Heading",background="#1e222d",foreground="#787b86",relief="flat",padding=(10,9),font=("Segoe UI",9,"bold"))
        style.map("Treeview",background=[("selected","#263342")],foreground=[("selected","#ffffff")])
        style.configure("TProgressbar",troughcolor="#1e222d",background="#26a69a",bordercolor="#2a2e39",lightcolor="#26a69a",darkcolor="#26a69a")
        style.configure("Status.TLabel",background="#1e222d",foreground="#26a69a",padding=(10,7))
    def make_ui(self):
        top=tk.Frame(self,bg="#131722",height=64); top.pack(fill="x"); top.pack_propagate(False)
        tk.Label(top,text="NSE",bg="#131722",fg="#26a69a",font=("Segoe UI",19,"bold")).pack(side="left",padx=(20,4))
        tk.Label(top,text="BULLISH TERMINAL",bg="#131722",fg="#f5f7fa",font=("Segoe UI",17,"bold")).pack(side="left")
        tk.Label(top,text=f"v{APP_VERSION}",bg="#131722",fg="#787b86",font=("Segoe UI",9)).pack(side="left",padx=12)
        self.status=ttk.Label(top,text="● READY",style="Status.TLabel"); self.status.pack(side="right",padx=16)
        ttk.Button(top,text="Check for Updates",command=self.update).pack(side="right",padx=6)

        nav=tk.Frame(self,bg="#131722",height=48); nav.pack(fill="x"); nav.pack_propagate(False)
        self.nav_buttons={}; self.pages={}
        for key,label in [("scanner","SCANNER"),("dash","REAL-TIME"),("paper","PAPER TRADING"),("bt","BACKTEST"),("setup","TRADE SETUP")]:
            btn=ttk.Button(nav,text=label,style="Nav.TButton",command=lambda k=key:self.show_page(k))
            btn.pack(side="left",padx=(12 if not self.nav_buttons else 2,2)); self.nav_buttons[key]=btn

        content=tk.Frame(self,bg="#0d1117"); content.pack(fill="both",expand=True,padx=12,pady=10)
        scan=tk.Frame(content,bg="#0d1117"); dash=tk.Frame(content,bg="#0d1117"); paper=tk.Frame(content,bg="#0d1117"); bt=tk.Frame(content,bg="#0d1117"); setup=tk.Frame(content,bg="#0d1117")
        self.pages={"scanner":scan,"dash":dash,"paper":paper,"bt":bt,"setup":setup}

        controls=tk.Frame(scan,bg="#0d1117"); controls.pack(fill="x",pady=(0,10))
        tk.Label(controls,text="TIMEFRAME",bg="#0d1117",fg="#787b86",font=("Segoe UI",9,"bold")).pack(side="left")
        self.tf=ttk.Combobox(controls,values=["15 min","1 hour","1 day"],state="readonly",width=10); self.tf.set("15 min"); self.tf.pack(side="left",padx=7)
        tk.Label(controls,text="UNIVERSE",bg="#0d1117",fg="#787b86",font=("Segoe UI",9,"bold")).pack(side="left",padx=(16,5))
        self.universe=ttk.Combobox(controls,values=["All NSE","Nifty 50","Nifty 100","Nifty 200"],state="readonly",width=12); self.universe.set("All NSE"); self.universe.pack(side="left",padx=5)
        tk.Label(controls,text="MIN SCORE / 100",bg="#0d1117",fg="#787b86",font=("Segoe UI",9,"bold")).pack(side="left",padx=(16,5))
        self.score=tk.IntVar(value=70); ttk.Spinbox(controls,from_=50,to=100,increment=5,textvariable=self.score,width=5).pack(side="left")
        ttk.Button(controls,text="REFRESH LIST",command=self.refresh_universe).pack(side="left",padx=7)
        ttk.Button(controls,text="⚡ SCAN MARKET",command=self.scan,style="Accent.TButton").pack(side="left",padx=7)

        cards=tk.Frame(scan,bg="#0d1117"); cards.pack(fill="x",pady=(0,10)); self.scan_kpis={}
        for title,key in [("STOCKS SCANNED","scanned"),("BULLISH SIGNALS","bullish"),("QUALIFIED","confirmed"),("NEAR-CONFIRMATION","candidates"),("SCAN DURATION","duration")]:
            card=tk.Frame(cards,bg="#1e222d",highlightbackground="#2a2e39",highlightthickness=1); card.pack(side="left",fill="both",expand=True,padx=4,ipady=7)
            tk.Label(card,text=title,bg="#1e222d",fg="#787b86",font=("Segoe UI",9,"bold")).pack(anchor="w",padx=14,pady=(7,0))
            v=tk.StringVar(value="0"); self.scan_kpis[key]=v
            tk.Label(card,textvariable=v,bg="#1e222d",fg="#f5f7fa",font=("Segoe UI",20,"bold")).pack(anchor="w",padx=14,pady=(2,7))

        prog=tk.Frame(scan,bg="#131722",highlightbackground="#2a2e39",highlightthickness=1); prog.pack(fill="x",pady=(0,10))
        self.scan_progress=ttk.Progressbar(prog,mode="determinate",maximum=100); self.scan_progress.pack(fill="x",padx=12,pady=(0,9))
        self.progress_label=tk.Label(prog,text="READY — press Scan Market",bg="#131722",fg="#787b86",font=("Segoe UI",9)); self.progress_label.pack(anchor="w",padx=12,pady=8)

        body=tk.Frame(scan,bg="#0d1117"); body.pack(fill="both",expand=True)
        left=tk.Frame(body,bg="#131722",highlightbackground="#2a2e39",highlightthickness=1); left.pack(side="left",fill="both",expand=True)
        right=tk.Frame(body,bg="#1e222d",width=370,highlightbackground="#2a2e39",highlightthickness=1); right.pack(side="right",fill="y",padx=(10,0)); right.pack_propagate(False)
        cols=["Symbol","Price","Composite","RSI","ADX","RelVol","EMA20","EMA50","VWAP","Trend","MACD","Signal"]
        self.tree=ttk.Treeview(left,columns=cols,show="headings")
        for x in cols: self.tree.heading(x,text=x.upper()); self.tree.column(x,width=88,anchor="center")
        self.tree.column("Symbol",width=105); self.tree.column("Composite",width=90); self.tree.column("Signal",width=88)
        self.tree.pack(fill="both",expand=True,padx=1,pady=1); self.tree.bind("<<TreeviewSelect>>",self.on_stock_select)

        tk.Label(right,text="STOCK DETAILS",bg="#1e222d",fg="#f5f7fa",font=("Segoe UI",13,"bold")).pack(anchor="w",padx=18,pady=(18,2))
        self.detail_symbol=tk.StringVar(value="Select a stock"); tk.Label(right,textvariable=self.detail_symbol,bg="#1e222d",fg="#26a69a",font=("Segoe UI",20,"bold")).pack(anchor="w",padx=18)
        self.detail_signal=tk.StringVar(value="—"); tk.Label(right,textvariable=self.detail_signal,bg="#1e222d",fg="#f5f7fa",font=("Segoe UI",10,"bold")).pack(anchor="w",padx=18,pady=(2,12))
        self.detail_vars={}
        for lab,key in [("PRICE","price"),("COMPOSITE","score"),("RSI 14","rsi"),("ADX 14","adx"),("RELATIVE VOLUME","relvol"),("EMA 20","ema20"),("EMA 50","ema50"),("VWAP","vwap"),("SUPERTREND","st"),("MACD","macd"),("MACD SIGNAL","macdsig")]:
            row=tk.Frame(right,bg="#1e222d"); row.pack(fill="x",padx=18,pady=3)
            tk.Label(row,text=lab,bg="#1e222d",fg="#787b86",font=("Segoe UI",9)).pack(side="left")
            v=tk.StringVar(value="—"); self.detail_vars[key]=v
            tk.Label(row,textvariable=v,bg="#1e222d",fg="#d1d4dc",font=("Segoe UI",10,"bold")).pack(side="right")
        self.detail_setup=tk.StringVar(value="Trade setup will load after selecting a stock.")
        tk.Label(right,textvariable=self.detail_setup,bg="#1e222d",fg="#787b86",wraplength=320,justify="left").pack(anchor="w",padx=18,pady=14)

        self.build_dashboard_page(dash); self.build_paper_page(paper); self.build_backtest_page(bt); self.build_setup_page(setup); self.show_page("scanner")
    def build_dashboard_page(self,frame):
        # Real-time signal dashboard (paper/simulation only)
        dc=ttk.Frame(frame); dc.pack(fill="x",pady=(0,8))
        ttk.Label(dc,text="Universe").pack(side="left")
        self.dash_u=ttk.Combobox(dc,values=["Nifty 50","Nifty 100","Nifty 200","All NSE"],state="readonly",width=12); self.dash_u.set("Nifty 50"); self.dash_u.pack(side="left",padx=5)
        ttk.Label(dc,text="Timeframe").pack(side="left",padx=(15,5))
        self.dash_tf=ttk.Combobox(dc,values=["15 min","1 hour","1 day"],state="readonly",width=10); self.dash_tf.set("15 min"); self.dash_tf.pack(side="left")
        ttk.Label(dc,text="Min score").pack(side="left",padx=(15,5))
        self.dash_score=tk.IntVar(value=70); ttk.Spinbox(dc,from_=0,to=100,textvariable=self.dash_score,width=5).pack(side="left")
        ttk.Label(dc,text="Refresh (sec)").pack(side="left",padx=(15,5))
        self.dash_refresh=tk.IntVar(value=30); ttk.Spinbox(dc,from_=15,to=300,increment=5,textvariable=self.dash_refresh,width=6).pack(side="left")
        ttk.Button(dc,text="Start Dashboard",command=self.start_dashboard,style="Accent.TButton").pack(side="left",padx=10)
        ttk.Button(dc,text="Stop",command=self.stop_dashboard).pack(side="left")
        self.dash_status=ttk.Label(dc,text="● Stopped",style="Status.TLabel"); self.dash_status.pack(side="right")
        kpi=ttk.Frame(frame); kpi.pack(fill="x",pady=(0,8))
        self.dash_kpis={}
        for title,key in [("BUY SIGNALS","buy"),("EXIT SIGNALS","exit"),("WATCH","watch"),("OPEN P&L","open"),("REALIZED P&L","realized"),("TOTAL P&L","total")]:
            box=ttk.Frame(kpi,padding=8); box.pack(side="left",fill="x",expand=True,padx=3)
            ttk.Label(box,text=title,font=("Segoe UI",9,"bold")).pack(anchor="w")
            v=tk.StringVar(value="0"); self.dash_kpis[key]=v
            ttk.Label(box,textvariable=v,font=("Segoe UI",14,"bold")).pack(anchor="w",pady=(3,0))
        dcols=["Symbol","Price","Score","Signal","RSI","ADX","RelVol","Supertrend","Entry","Stop","Target","Bar Time"]
        self.dash_tree=ttk.Treeview(frame,columns=dcols,show="headings",height=16)
        for col in dcols:
            self.dash_tree.heading(col,text=col); self.dash_tree.column(col,width=100,anchor="center")
        self.dash_tree.column("Symbol",width=110); self.dash_tree.column("Bar Time",width=175)
        self.dash_tree.pack(fill="both",expand=True)
        ttk.Label(frame,text="Data source: Yahoo Finance via yfinance. Paper trading only; no real orders are sent. Yahoo data may be delayed.",wraplength=1200).pack(anchor="w",pady=8)


    def build_paper_page(self,frame):
        # Dummy frame trading tab
        pf=ttk.Frame(frame); pf.pack(fill="x",pady=5)
        ttk.Label(pf,text="Stock").pack(side="left"); self.paper_symbol=ttk.Entry(pf,width=14); self.paper_symbol.pack(side="left",padx=5)
        ttk.Label(pf,text="Qty").pack(side="left",padx=(12,5)); self.paper_qty=tk.IntVar(value=1); ttk.Spinbox(pf,from_=1,to=100000,textvariable=self.paper_qty,width=8).pack(side="left")
        ttk.Label(pf,text="Price").pack(side="left",padx=(12,5)); self.paper_price=ttk.Entry(pf,width=12); self.paper_price.pack(side="left",padx=5)
        ttk.Button(pf,text="BUY (Paper)",command=lambda:self.paper_order("BUY"),style="Accent.TButton").pack(side="left",padx=8)
        ttk.Button(pf,text="SELL (Paper)",command=lambda:self.paper_order("SELL")).pack(side="left")
        ttk.Button(pf,text="Refresh Prices",command=self.refresh_paper_prices).pack(side="left",padx=8)
        self.paper_status=ttk.Label(frame,text="Paper account starts at ₹1,00,000. No real order will be sent.",style="Status.TLabel"); self.paper_status.pack(fill="x",pady=8)
        self.paper_summary=ttk.Label(frame,text="Cash: ₹1,00,000 | Invested: ₹0 | Realized P&L: ₹0 | Unrealized P&L: ₹0 | Total P&L: ₹0",font=("Segoe UI",11,"bold")); self.paper_summary.pack(fill="x",pady=5)
        pcols=["Symbol","Qty","Avg Buy","LTP","Invested","Market Value","Unrealized P&L","Return %"]
        self.paper_tree=ttk.Treeview(frame,columns=pcols,show="headings")
        for col in pcols: self.paper_tree.heading(col,text=col); self.paper_tree.column(col,width=135,anchor="center")
        self.paper_tree.pack(fill="both",expand=True,pady=8)
        tcols=["Time","Action","Symbol","Qty","Price","Value","Realized P&L"]
        self.paper_trades_tree=ttk.Treeview(frame,columns=tcols,show="headings",height=8)
        for col in tcols: self.paper_trades_tree.heading(col,text=col); self.paper_trades_tree.column(col,width=130,anchor="center")
        self.paper_trades_tree.pack(fill="x")
        self.paper_cash=100000.0; self.paper_positions={}; self.paper_trades=[]; self.paper_realized=0.0


    def build_backtest_page(self,frame):
        f=ttk.Frame(frame); f.pack(fill="x")
        ttk.Label(f,text="Stock Universe").grid(row=0,column=0); self.btu=ttk.Combobox(f,values=["All NSE","Nifty 50","Nifty 100","Nifty 200"],state="readonly",width=12); self.btu.set("Nifty 50"); self.btu.grid(row=0,column=1,padx=5); ttk.Label(f,text="Timeframe").grid(row=0,column=2); self.btf=ttk.Combobox(f,values=["15 min","1 hour","1 day"],state="readonly",width=10); self.btf.set("1 day"); self.btf.grid(row=0,column=3,padx=5)
        ttk.Label(f,text="Start").grid(row=0,column=4); self.start=ttk.Entry(f,width=12); self.start.insert(0,(datetime.now()-timedelta(days=365)).strftime("%Y-%m-%d")); self.start.grid(row=0,column=5,padx=5)
        ttk.Label(f,text="End").grid(row=0,column=6); self.end=ttk.Entry(f,width=12); self.end.insert(0,datetime.now().strftime("%Y-%m-%d")); self.end.grid(row=0,column=7,padx=5)
        ttk.Label(f,text="Score").grid(row=1,column=0); self.bs=tk.IntVar(value=6); ttk.Spinbox(f,from_=1,to=7,textvariable=self.bs,width=5).grid(row=1,column=1)
        ttk.Label(f,text="Target %").grid(row=1,column=2); self.target=tk.DoubleVar(value=2); ttk.Entry(f,textvariable=self.target,width=8).grid(row=1,column=3)
        ttk.Label(f,text="Stop %").grid(row=1,column=4); self.stop=tk.DoubleVar(value=1); ttk.Entry(f,textvariable=self.stop,width=8).grid(row=1,column=5)
        ttk.Label(f,text="Max bars").grid(row=1,column=6); self.bars=tk.IntVar(value=10); ttk.Entry(f,textvariable=self.bars,width=8).grid(row=1,column=7)
        ttk.Button(f,text="Run Backtest",command=self.backtest).grid(row=0,column=8,rowspan=2,padx=10)
        ttk.Button(f,text="Export CSV",command=self.export).grid(row=0,column=9,rowspan=2)
        self.summary=ttk.Label(frame,text="No backtest run yet.",font=("Segoe UI",11,"bold")); self.summary.pack(fill="x",pady=8)
        cols2=["Symbol","SignalTime","Entry","Exit","Return %","Outcome","Score","Bars"]
        self.bt_tree=ttk.Treeview(frame,columns=cols2,show="headings")
        for x in cols2: self.bt_tree.heading(x,text=x); self.bt_tree.column(x,width=135,anchor="center")
        self.bt_tree.pack(fill="both",expand=True)


    def build_setup_page(self,frame):
        # Single-stock trade frame
        sf=ttk.Frame(frame); sf.pack(fill="x",pady=5)
        ttk.Label(sf,text="NSE Stock").pack(side="left")
        self.setup_symbol=ttk.Entry(sf,width=16); self.setup_symbol.pack(side="left",padx=5)
        ttk.Label(sf,text="Timeframe").pack(side="left",padx=(15,5))
        self.setup_tf=ttk.Combobox(sf,values=["15 min","1 hour","1 day"],state="readonly",width=10)
        self.setup_tf.set("1 day"); self.setup_tf.pack(side="left")
        ttk.Button(sf,text="Calculate Entry & Exit",command=self.calculate_setup).pack(side="left",padx=12)
        ttk.Button(sf,text="Use Selected Stock",command=self.use_selected_stock).pack(side="left")

        self.setup_summary=ttk.Label(frame,text="Select a stock and calculate its trade frame.",font=("Segoe UI",12,"bold"))
        self.setup_summary.pack(fill="x",pady=15)
        sg=ttk.Frame(frame); sg.pack(fill="x")
        labels=[
            ("Current Price","setup_price"),("Suggested Entry","setup_entry"),
            ("Stop Loss","setup_stop"),("Target","setup_target"),
            ("Risk / Share","setup_risk"),("Reward / Share","setup_reward"),
            ("Risk : Reward","setup_rr"),("ATR(14)","setup_atr"),
            ("20-Bar Breakout","setup_breakout"),("Scanner Score","setup_score"),
            ("RSI","setup_rsi"),("ADX","setup_adx"),("Rel Volume","setup_relvol"),
            ("Supertrend","setup_st"),("MACD","setup_macd")
        ]
        self.setup_vars={}
        for i,(lab,key) in enumerate(labels):
            r=i//4; c=(i%4)*2
            ttk.Label(sg,text=lab).grid(row=r,column=c,sticky="w",padx=8,pady=8)
            v=tk.StringVar(value="-"); self.setup_vars[key]=v
            ttk.Label(sg,textvariable=v,font=("Segoe UI",10,"bold")).grid(row=r,column=c+1,sticky="w",padx=8,pady=8)
        ttk.Label(frame,text="Method: long frame using current price as entry, 1.5x ATR stop and 3x ATR target (2R). These are algorithmic reference levels, not guaranteed prices.",wraplength=1100).pack(anchor="w",pady=18)


    def show_page(self,key):
        page=self.pages.get(key)
        if page is None:
            return
        for p in self.pages.values():
            p.pack_forget()
        page.pack(fill="both",expand=True)
        for k,btn in self.nav_buttons.items():
            btn.configure(style="Nav.Active.TButton" if k==key else "Nav.TButton")
        self.current_page=key

    def on_stock_select(self,event=None):
        sel=self.tree.selection()
        if not sel: return
        vals=self.tree.item(sel[0],"values")
        if not vals: return
        self.detail_symbol.set(str(vals[0]))
        self.detail_signal.set(f"Signal: {vals[11]}  |  Composite: {vals[2]}")
        for key,val in zip(["price","score","rsi","adx","relvol","ema20","ema50","vwap","st","macd"],vals[:10]):
            self.detail_vars[key].set(str(val))
        self.detail_vars["macdsig"].set("Loading...")
        self.detail_setup.set("Loading Trade Setup...")
        threading.Thread(target=self.detail_worker,args=(str(vals[0]),self.tf.get()),daemon=True).start()

    def detail_worker(self,sym,tf):
        try: self.q.put(("detail",trade_setup(sym,tf)))
        except Exception: self.q.put(("detail_error",""))

    def use_selected_stock(self):
        sel=self.tree.selection()
        if not sel:
            messagebox.showinfo("Trade Setup","Select a stock in Live Scanner first.")
            return
        vals=self.tree.item(sel[0],"values")
        if vals:
            self.setup_symbol.delete(0,"end"); self.setup_symbol.insert(0,vals[0])

    def calculate_setup(self):
        sym=self.setup_symbol.get().strip().upper()
        if not sym:
            messagebox.showinfo("Trade Setup","Enter an NSE stock symbol, for example RELIANCE.")
            return
        self.setup_summary.config(text=f"Calculating setup for {sym}...")
        threading.Thread(target=self.setup_worker,args=(sym,self.setup_tf.get()),daemon=True).start()

    def setup_worker(self,sym,tf):
        try:
            result=trade_setup(sym,tf)
            self.q.put(("setup",result))
        except Exception as e:
            log("trade_setup: "+traceback.format_exc())
            self.q.put(("msg",f"Trade setup failed for {sym}:\n{e}"))


    def start_dashboard(self):
        self.dash_running=True; self.dash_status.config(text="● Running"); self.dash_worker()

    def stop_dashboard(self):
        self.dash_running=False; self.dash_status.config(text="● Stopped")

    def dash_worker(self):
        if not getattr(self,"dash_running",False): return
        threading.Thread(target=self.dashboard_fetch_worker,daemon=True).start()

    def dashboard_fetch_worker(self):
        try:
            universe=self.dash_u.get(); tf=self.dash_tf.get(); minimum=self.dash_score.get()
            symbols=get_index_symbols(universe)
            interval={"15 min":"15m","1 hour":"60m","1 day":"1d"}[tf]
            period={"15 min":"60d","1 hour":"730d","1 day":"1y"}[tf]
            results=[]
            for i in range(0,len(symbols),75):
                if not getattr(self,"dash_running",False): break
                batch=symbols[i:i+75]
                data_map=fetch_batch(batch,interval,period)
                daily_map=fetch_batch(batch,"1d","60d")
                for sym,d in data_map.items():
                    try:
                        snap=build_signal_snapshot(sym,d,get_market_regime(),daily_map.get(sym))
                        if not snap: continue
                        x=snap["latest"]; cd=snap["data"]; score=snap["score"]; liquid=snap["liquid"]
                        price=float(x.Close); atrv=float(atr(cd,14).iloc[-1])
                        qualified=bool(snap["signal"].qualifies and liquid and score>=minimum)
                        results.append({"symbol":sym,"price":price,"score":score,
                                        "signal":"BUY" if qualified else "WATCH",
                                        "rsi":float(x.RSI14),"adx":float(x.ADX14),"relvol":float(x.RelVol),
                                        "st":"BULLISH" if x.STDir==1 else "BEARISH",
                                        "entry":price,"stop":max(.01,price-1.5*atrv),"target":price+3*atrv,
                                        "time":str(cd.index[-1]),"liquid":liquid})
                    except Exception as e: log(f"dashboard {sym}: {e}")
            results.sort(key=lambda r:(0 if r["signal"]=="BUY" else 1,-r["score"],r["symbol"]))
            self.q.put(("dashboard",results))
        except Exception as e:
            log("dashboard_worker: "+traceback.format_exc()); self.q.put(("msg",f"Dashboard refresh failed:\n{e}"))

    def dashboard_render(self,results):
        for item in self.dash_tree.get_children(): self.dash_tree.delete(item)
        buys=sum(r["signal"]=="BUY" for r in results); exits=sum(r["signal"]=="EXIT" for r in results); watches=sum(r["signal"]=="WATCH" for r in results)
        for r in results[:100]:
            self.dash_tree.insert("", "end", values=(r["symbol"],f"{r['price']:.2f}",r["score"],r["signal"],f"{r['rsi']:.1f}",f"{r['adx']:.1f}",f"{r['relvol']:.2f}",r["st"],f"{r['entry']:.2f}",f"{r['stop']:.2f}",f"{r['target']:.2f}",r["time"]))
        self.dash_kpis["buy"].set(str(buys)); self.dash_kpis["exit"].set(str(exits)); self.dash_kpis["watch"].set(str(watches))
        self.update_paper_positions()
        self.dash_status.config(text=f"● Updated {datetime.now():%H:%M:%S} | {len(results)} stocks")
        if getattr(self,"dash_running",False): self.after(max(15,int(self.dash_refresh.get()))*1000,self.dash_worker)

    def paper_order(self,action):
        sym=self.paper_symbol.get().strip().upper(); qty=int(self.paper_qty.get() or 0)
        if not sym or qty<=0: messagebox.showinfo("Paper Trading","Enter a valid stock and quantity."); return
        try: price=float(self.paper_price.get()) if self.paper_price.get().strip() else None
        except Exception: price=None
        if price is None or price<=0:
            try:
                d=fetch(sym,"15m",period="5d"); price=float(d.Close.iloc[-1]) if not d.empty else 0
            except Exception: price=0
        if price<=0: messagebox.showerror("Paper Trading","Could not get a price. Enter the price manually."); return
        if action=="BUY":
            cost=price*qty
            if cost>self.paper_cash: messagebox.showerror("Paper Trading",f"Insufficient paper cash. Available ₹{self.paper_cash:,.2f}."); return
            p=self.paper_positions.get(sym,{"qty":0,"avg":0.0}); newqty=p["qty"]+qty; p["avg"]=((p["avg"]*p["qty"])+(price*qty))/newqty; p["qty"]=newqty; self.paper_positions[sym]=p; self.paper_cash-=cost; realized=0.0
        else:
            p=self.paper_positions.get(sym)
            if not p or p["qty"]<qty: messagebox.showerror("Paper Trading","You do not hold enough shares to sell."); return
            realized=(price-p["avg"])*qty; self.paper_realized+=realized; self.paper_cash+=price*qty; p["qty"]-=qty
            if p["qty"]==0: del self.paper_positions[sym]
        stamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.paper_trades.append((stamp,action,sym,qty,price,price*qty,realized))
        self.paper_trades_tree.insert("",0,values=(stamp,action,sym,qty,f"{price:.2f}",f"₹{price*qty:,.2f}",f"₹{realized:,.2f}"))
        self.paper_status.config(text=f"{action} {qty} {sym} @ ₹{price:.2f} — PAPER ONLY")
        self.update_paper_positions()

    def refresh_paper_prices(self):
        self.update_paper_positions(force=True)

    def update_paper_positions(self,force=False):
        total_unreal=0.0; invested=0.0
        for item in self.paper_tree.get_children(): self.paper_tree.delete(item)
        for sym,p in list(getattr(self,"paper_positions",{}).items()):
            try:
                d=fetch(sym,"15m",period="5d"); ltp=float(d.Close.iloc[-1]) if not d.empty else p["avg"]
            except Exception: ltp=p["avg"]
            inv=p["avg"]*p["qty"]; mv=ltp*p["qty"]; pnl=mv-inv; invested+=inv; total_unreal+=pnl
            self.paper_tree.insert("", "end", values=(sym,p["qty"],f"{p['avg']:.2f}",f"{ltp:.2f}",f"₹{inv:,.2f}",f"₹{mv:,.2f}",f"₹{pnl:,.2f}",f"{(pnl/inv*100 if inv else 0):.2f}%"))
        total=self.paper_realized+total_unreal
        self.paper_summary.config(text=f"Cash: ₹{self.paper_cash:,.2f} | Invested: ₹{invested:,.2f} | Realized P&L: ₹{self.paper_realized:,.2f} | Unrealized P&L: ₹{total_unreal:,.2f} | Total P&L: ₹{total:,.2f}")
        if hasattr(self,"dash_kpis"):
            self.dash_kpis["open"].set(f"₹{total_unreal:,.0f}"); self.dash_kpis["realized"].set(f"₹{self.paper_realized:,.0f}"); self.dash_kpis["total"].set(f"₹{total:,.0f}")

    def use_selected_stock(self):
        sel=self.tree.selection()
        if not sel:
            messagebox.showinfo("Trade Setup","Select a stock in Live Scanner first.")
            return
        vals=self.tree.item(sel[0],"values")
        if vals:
            self.setup_symbol.delete(0,"end"); self.setup_symbol.insert(0,vals[0])

    def calculate_setup(self):
        sym=self.setup_symbol.get().strip().upper()
        if not sym:
            messagebox.showinfo("Trade Setup","Enter an NSE stock symbol, for example RELIANCE.")
            return
        self.setup_summary.config(text=f"Calculating setup for {sym}...")
        threading.Thread(target=self.setup_worker,args=(sym,self.setup_tf.get()),daemon=True).start()

    def setup_worker(self,sym,tf):
        try:
            result=trade_setup(sym,tf)
            self.q.put(("setup",result))
        except Exception as e:
            log("trade_setup: "+traceback.format_exc())
            self.q.put(("msg",f"Trade setup failed for {sym}:\n{e}"))


    def start_dashboard(self):
        self.dash_running=True; self.dash_status.config(text="● Running"); self.dash_worker()

    def stop_dashboard(self):
        self.dash_running=False; self.dash_status.config(text="● Stopped")

    def dash_worker(self):
        if not getattr(self,"dash_running",False): return
        threading.Thread(target=self.dashboard_fetch_worker,daemon=True).start()

    def dashboard_fetch_worker(self):
        try:
            universe=self.dash_u.get(); tf=self.dash_tf.get(); minimum=self.dash_score.get()
            symbols=get_index_symbols(universe)
            interval={"15 min":"15m","1 hour":"60m","1 day":"1d"}[tf]
            period={"15 min":"60d","1 hour":"730d","1 day":"1y"}[tf]
            results=[]
            for i in range(0,len(symbols),75):
                if not getattr(self,"dash_running",False): break
                data_map=fetch_batch(symbols[i:i+75],interval,period)
                for sym,d in data_map.items():
                    try:
                        if len(d)<60: continue
                        cd=calc(d); x=cd.iloc[-1]
                        cond=[x.Close>x.EMA20 and x.EMA20>x.EMA50,x.Close>x.VWAP,x.RSI14>50,x.ADX14>=20,x.RelVol>=1.2,x.STDir==1,x.MACD>x.MACDSignal]
                        score=int(sum(bool(v) for v in cond)); bullish=bool(x.STDir==1)
                        atrv=float(atr(cd,14).iloc[-1]); price=float(x.Close)
                        results.append({"symbol":sym,"price":price,"score":score,"signal":"BUY" if score>=minimum and bullish else ("EXIT" if score<=3 or not bullish else "WATCH"),"rsi":float(x.RSI14),"adx":float(x.ADX14),"relvol":float(x.RelVol),"st":"BULLISH" if bullish else "BEARISH","entry":price,"stop":max(.01,price-1.5*atrv),"target":price+3*atrv,"time":str(cd.index[-1])})
                    except Exception as e: log(f"dashboard {sym}: {e}")
            results.sort(key=lambda r:(0 if r["signal"]=="BUY" else 1 if r["signal"]=="EXIT" else 2,-r["score"],r["symbol"]))
            self.q.put(("dashboard",results))
        except Exception as e:
            log("dashboard_worker: "+traceback.format_exc()); self.q.put(("msg",f"Dashboard refresh failed:\n{e}"))

    def dashboard_render(self,results):
        for item in self.dash_tree.get_children(): self.dash_tree.delete(item)
        buys=sum(r["signal"]=="BUY" for r in results); exits=sum(r["signal"]=="EXIT" for r in results); watches=sum(r["signal"]=="WATCH" for r in results)
        for r in results[:100]:
            self.dash_tree.insert("", "end", values=(r["symbol"],f"{r['price']:.2f}",r["score"],r["signal"],f"{r['rsi']:.1f}",f"{r['adx']:.1f}",f"{r['relvol']:.2f}",r["st"],f"{r['entry']:.2f}",f"{r['stop']:.2f}",f"{r['target']:.2f}",r["time"]))
        self.dash_kpis["buy"].set(str(buys)); self.dash_kpis["exit"].set(str(exits)); self.dash_kpis["watch"].set(str(watches))
        self.update_paper_positions()
        self.dash_status.config(text=f"● Updated {datetime.now():%H:%M:%S} | {len(results)} stocks")
        if getattr(self,"dash_running",False): self.after(max(15,int(self.dash_refresh.get()))*1000,self.dash_worker)

    def paper_order(self,action):
        sym=self.paper_symbol.get().strip().upper(); qty=int(self.paper_qty.get() or 0)
        if not sym or qty<=0: messagebox.showinfo("Paper Trading","Enter a valid stock and quantity."); return
        try: price=float(self.paper_price.get()) if self.paper_price.get().strip() else None
        except Exception: price=None
        if price is None or price<=0:
            try:
                d=fetch(sym,"15m",period="5d"); price=float(d.Close.iloc[-1]) if not d.empty else 0
            except Exception: price=0
        if price<=0: messagebox.showerror("Paper Trading","Could not get a price. Enter the price manually."); return
        if action=="BUY":
            cost=price*qty
            if cost>self.paper_cash: messagebox.showerror("Paper Trading",f"Insufficient paper cash. Available ₹{self.paper_cash:,.2f}."); return
            p=self.paper_positions.get(sym,{"qty":0,"avg":0.0}); newqty=p["qty"]+qty; p["avg"]=((p["avg"]*p["qty"])+(price*qty))/newqty; p["qty"]=newqty; self.paper_positions[sym]=p; self.paper_cash-=cost; realized=0.0
        else:
            p=self.paper_positions.get(sym)
            if not p or p["qty"]<qty: messagebox.showerror("Paper Trading","You do not hold enough shares to sell."); return
            realized=(price-p["avg"])*qty; self.paper_realized+=realized; self.paper_cash+=price*qty; p["qty"]-=qty
            if p["qty"]==0: del self.paper_positions[sym]
        stamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.paper_trades.append((stamp,action,sym,qty,price,price*qty,realized))
        self.paper_trades_tree.insert("",0,values=(stamp,action,sym,qty,f"{price:.2f}",f"₹{price*qty:,.2f}",f"₹{realized:,.2f}"))
        self.paper_status.config(text=f"{action} {qty} {sym} @ ₹{price:.2f} — PAPER ONLY")
        self.update_paper_positions()

    def refresh_paper_prices(self):
        self.update_paper_positions(force=True)

    def update_paper_positions(self,force=False):
        total_unreal=0.0; invested=0.0
        for item in self.paper_tree.get_children(): self.paper_tree.delete(item)
        for sym,p in list(getattr(self,"paper_positions",{}).items()):
            try:
                d=fetch(sym,"15m",period="5d"); ltp=float(d.Close.iloc[-1]) if not d.empty else p["avg"]
            except Exception: ltp=p["avg"]
            inv=p["avg"]*p["qty"]; mv=ltp*p["qty"]; pnl=mv-inv; invested+=inv; total_unreal+=pnl
            self.paper_tree.insert("", "end", values=(sym,p["qty"],f"{p['avg']:.2f}",f"{ltp:.2f}",f"₹{inv:,.2f}",f"₹{mv:,.2f}",f"₹{pnl:,.2f}",f"{(pnl/inv*100 if inv else 0):.2f}%"))
        total=self.paper_realized+total_unreal
        self.paper_summary.config(text=f"Cash: ₹{self.paper_cash:,.2f} | Invested: ₹{invested:,.2f} | Realized P&L: ₹{self.paper_realized:,.2f} | Unrealized P&L: ₹{total_unreal:,.2f} | Total P&L: ₹{total:,.2f}")
        if hasattr(self,"dash_kpis"):
            self.dash_kpis["open"].set(f"₹{total_unreal:,.0f}"); self.dash_kpis["realized"].set(f"₹{self.paper_realized:,.0f}"); self.dash_kpis["total"].set(f"₹{total:,.0f}")

    def refresh_universe(self):
        def worker():
            try:
                name=self.universe.get(); syms=get_index_symbols(name,force=True)
                self.q.put(("status",f"{name} list refreshed: {len(syms)} stocks available."))
            except Exception as e:
                self.q.put(("msg",f"Could not refresh stock list:\n{e}"))
        threading.Thread(target=worker,daemon=True).start()

    def scan(self):
        for x in self.tree.get_children(): self.tree.delete(x)
        self.stop_flag=False; self.scan_started=datetime.now()
        for v in self.scan_kpis.values(): v.set('0')
        self.scan_kpis['duration'].set('—'); self.scan_progress['value']=0
        threading.Thread(target=self.scan_worker,daemon=True).start()

    def scan_worker(self):
        try:
            interval,period={"15 min":("15m","60d"),"1 hour":("60m","730d"),"1 day":("1d","10y")}[self.tf.get()]
            universe=self.universe.get()
            symbols=get_index_symbols(universe)
            regime=get_market_regime()
            self.q.put(("status",f"Loaded {len(symbols)} stocks from {universe}. Checking market data, liquidity and signals..."))
            batch_size=100
            batches=[symbols[i:i+batch_size] for i in range(0,len(symbols),batch_size)]
            daily_map={}
            with ThreadPoolExecutor(max_workers=8) as daily_pool:
                daily_futures=[daily_pool.submit(fetch_batch,b,"1d","60d") for b in batches]
                for fut in as_completed(daily_futures):
                    daily_map.update(fut.result())
            rows=[]; completed=0; minimum=self.score.get()
            self.q.put(('scan_total',len(symbols)))
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures=[pool.submit(fetch_batch,b,interval,period) for b in batches]
                for fut in as_completed(futures):
                    if self.stop_flag: break
                    data_map=fut.result()
                    for sym,d in data_map.items():
                        snap=build_signal_snapshot(sym,d,regime,daily_map.get(sym))
                        if not snap: continue
                        rows.append((snap["score"],sym,snap["row"],snap["signal"].qualifies,snap["liquid"]))
                    completed+=len(data_map)
                    confirmed=sum(1 for sc,_,_,q,l in rows if q and l and sc>=minimum)
                    candidates=sum(1 for sc,_,_,q,l in rows if l and sc>=max(0,minimum-10) and not (q and l and sc>=minimum))
                    self.q.put(('scan_progress',completed,len(symbols),confirmed,candidates))
            rows.sort(key=lambda z:(0 if z[3] and z[4] and z[0]>=minimum else 1, -z[0], z[1]))
            for _,_,row,_,_ in rows:
                self.q.put(("row",row))
            buys=sum(1 for sc,_,_,q,l in rows if q and l and sc>=minimum)
            liquid_count=sum(1 for _,_,_,_,l in rows if l)
            if rows:
                self.q.put(("status",f"Scan complete — {len(rows)} stocks with valid data | {buys} BUY candidates | {liquid_count} passed liquidity filter."))
            else:
                self.q.put(("status","Scan complete — no stock returned usable market data."))
        except Exception as e:
            log("scan_worker: "+traceback.format_exc())
            self.q.put(("msg",f"Scan failed:\n{e}"))

    def backtest(self):
        for x in self.bt_tree.get_children(): self.bt_tree.delete(x)
        self.trades=[]; threading.Thread(target=self.bt_worker,daemon=True).start()

    def bt_worker(self):
        try:
            interval={"15 min":"15m","1 hour":"60m","1 day":"1d"}[self.btf.get()]
            universe=self.btu.get(); symbols=get_index_symbols(universe); total=len(symbols)
            self.q.put(("status",f"Backtesting {total} stocks from {universe}..."))
            for idx,sym in enumerate(symbols,1):
                self.q.put(("status",f"Backtesting {idx}/{total}: {sym}"))
                try:
                    d=fetch(sym,interval,start=self.start.get(),end=self.end.get())
                    if d.empty or len(d)<61: continue
                    d=calc(d)
                    regime_data=fetch_historical_market_regime(
                        (pd.to_datetime(self.start.get())-timedelta(days=80)).strftime("%Y-%m-%d"),
                        (pd.to_datetime(self.end.get())+timedelta(days=2)).strftime("%Y-%m-%d"),
                    )
                    daily=fetch(sym,"1d",
                                start=(pd.to_datetime(self.start.get())-timedelta(days=30)).strftime("%Y-%m-%d"),
                                end=(pd.to_datetime(self.end.get())+timedelta(days=2)).strftime("%Y-%m-%d"))
                    for i in range(60,len(d)-1):
                        x=d.iloc[i]
                        regime=historical_market_regime(regime_data,d.index[i])
                        prior_daily=daily.loc[daily.index<=pd.Timestamp(d.index[i])].tail(20) if not daily.empty else daily
                        avg_volume,avg_value=liquidity_metrics(prior_daily)
                        if not passes_liquidity_filter(avg_volume,avg_value): continue
                        signal=evaluate_signal(
                            price=float(x.Close), ema20=float(x.EMA20), ema50=float(x.EMA50),
                            vwap=float(x.VWAP), rsi14=float(x.RSI14), adx14=float(x.ADX14),
                            relative_volume=float(x.RelVol), supertrend_bullish=bool(x.STDir==1),
                            macd=float(x.MACD), macd_signal=float(x.MACDSignal), regime=regime,
                        )
                        score=signal.composite_score
                        if not signal.qualifies or score<self.bs.get(): continue
                        entry=float(d.Open.iloc[i+1]); target=entry*(1+self.target.get()/100); stop=entry*(1-self.stop.get()/100)
                        last=min(len(d)-1,i+1+self.bars.get()); outcome="TIME"; exitp=float(d.Close.iloc[last]); exit_i=last
                        for j in range(i+1,last+1):
                            if float(d.Low.iloc[j])<=stop: exitp=stop; outcome="LOSS"; exit_i=j; break
                            if float(d.High.iloc[j])>=target: exitp=target; outcome="WIN"; exit_i=j; break
                        ret=(exitp/entry-1)*100
                        self.trades.append({"Symbol":sym,"SignalTime":str(d.index[i]),"Entry":entry,"Exit":exitp,"ReturnPct":ret,"Outcome":outcome,"CompositeScore":score,"BarsHeld":exit_i-(i+1)})
                except Exception as e: log(f"backtest {sym}: {e}")
            self.q.put(("done",self.trades)); self.q.put(("status",f"Backtest complete - {len(self.trades)} trades from {universe}."))
        except Exception as e:
            log("bt_worker: "+traceback.format_exc()); self.q.put(("msg",f"Backtest failed: {e}"))
    def export(self):
        if not self.trades: messagebox.showinfo("Backtest","Run a backtest first."); return
        p=filedialog.asksaveasfilename(defaultextension=".csv",filetypes=[("CSV","*.csv")])
        if p: pd.DataFrame(self.trades).to_csv(p,index=False)

    def update(self):
        threading.Thread(target=self.update_worker,daemon=True).start()

    def update_worker(self):
        try:
            info=check_update()
            if not info: self.q.put(("msg","You already have the latest version.")); return
            self.q.put(("update",info))
        except Exception as e:
            log("update: "+traceback.format_exc()); self.q.put(("msg",f"Update check failed:\n{e}"))

    def poll(self):
        try:
            while True:
                typ,data=self.q.get_nowait()
                if typ in ("row","candidate"): self.tree.insert("", "end", values=data)
                elif typ=="status": self.status.config(text=data)
                elif typ=="scan_total":
                    self.scan_progress["maximum"]=max(1,data); self.scan_progress["value"]=0; self.progress_label.config(text=f"0 / {data} stocks scanned")
                elif typ=="scan_progress":
                    done,total,confirmed,candidates=data
                    self.scan_progress["maximum"]=max(1,total); self.scan_progress["value"]=done; self.progress_label.config(text=f"{done:,} / {total:,} stocks scanned")
                    self.scan_kpis["scanned"].set(f"{done:,}"); self.scan_kpis["bullish"].set(f"{confirmed+candidates:,}"); self.scan_kpis["confirmed"].set(f"{confirmed:,}"); self.scan_kpis["candidates"].set(f"{candidates:,}")
                    if self.scan_started: self.scan_kpis["duration"].set(str(datetime.now()-self.scan_started).split(".")[0])
                elif typ=="msg": messagebox.showinfo("NSE Bullish Scanner",data)
                elif typ=="dashboard":
                    self.dashboard_render(data)
                elif typ=="detail":
                    r=data; self.detail_vars["macdsig"].set(f"{r['macd_signal']:.3f}"); self.detail_setup.set(f"Entry Rs.{r['entry']:.2f} | Stop Rs.{r['stop']:.2f} | Target Rs.{r['target']:.2f} | R:R 1:{r['rr']:.2f}")
                elif typ=="detail_error":
                    self.detail_setup.set("Trade setup unavailable.")
                elif typ=="setup":
                    r=data
                    self.setup_summary.config(text=f"{r['symbol']} | {r['supertrend']} | Data as of {r['time']}")
                    vals={
                        "setup_price":f"{r['price']:.2f}","setup_entry":f"{r['entry']:.2f}",
                        "setup_stop":f"{r['stop']:.2f}","setup_target":f"{r['target']:.2f}",
                        "setup_risk":f"{r['risk']:.2f}","setup_reward":f"{r['reward']:.2f}",
                        "setup_rr":f"1 : {r['rr']:.2f}","setup_atr":f"{r['atr']:.2f}",
                        "setup_breakout":f"{r['breakout']:.2f}","setup_score":f"{r['score']}/100",
                        "setup_rsi":f"{r['rsi']:.1f}","setup_adx":f"{r['adx']:.1f}",
                        "setup_relvol":f"{r['relvol']:.2f}","setup_st":r["supertrend"],"setup_macd":f"{r['macd']:.3f}"
                    }
                    for k,v in vals.items(): self.setup_vars[k].set(v)
                elif typ=="done":
                    self.trades=data
                    if data:
                        df=pd.DataFrame(data); wins=(df.ReturnPct>0).sum(); gp=df.loc[df.ReturnPct>0,"ReturnPct"].sum(); gl=abs(df.loc[df.ReturnPct<0,"ReturnPct"].sum())
                        self.summary.config(text=f"Trades: {len(df)} | Wins: {wins} | Win rate: {wins/len(df)*100:.2f}% | Avg return: {df.ReturnPct.mean():.2f}% | Profit factor: {(gp/gl if gl else float('inf')):.2f}")
                    else: self.summary.config(text="No signals found.")
                    for r in data: self.bt_tree.insert("", "end", values=(r["Symbol"],r["SignalTime"],f'{r["Entry"]:.2f}',f'{r["Exit"]:.2f}',f'{r["ReturnPct"]:.2f}%',r["Outcome"],r["CompositeScore"],r["BarsHeld"]))
                elif typ=="update":
                    info=data
                    if messagebox.askyesno("Update available",f"Version {info['version']} is available.\n\n{info.get('notes','')}\n\nUpdate now?"):
                        self.start_updater(info)
        except queue.Empty: pass
        self.after(200,self.poll)

    def start_updater(self,info):
        if not getattr(sys,"frozen",False):
            messagebox.showinfo("Update","Automatic updating works from the packaged EXE. Build/install the connected EXE first."); return
        updater=Path(sys.executable).with_name("NSE_Bullish_Scanner_Updater.exe")
        if not updater.exists():
            messagebox.showerror("Update","Updater component is missing. Please install the connected build."); return
        subprocess.Popen([str(updater),str(sys.executable),info["url"],info.get("sha256","")],creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        self.destroy()

def main():
    try:
        app=App(); app.mainloop()
    except Exception as e:
        fatal(e)

if __name__=="__main__":
    main()
