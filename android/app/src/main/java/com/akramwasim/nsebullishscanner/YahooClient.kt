package com.akramwasim.nsebullishscanner

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import kotlin.math.abs

data class CheckResult(val available:Boolean,val message:String,val url:String?)

class YahooClient {
    private val http=OkHttpClient.Builder().connectTimeout(8,TimeUnit.SECONDS).readTimeout(12,TimeUnit.SECONDS).build()
    suspend fun scan(symbol:String,timeframe:String):StockRow?=withContext(Dispatchers.IO){
        runCatching{
            val interval=when(timeframe){"15 min"->"15m";"1 hour"->"60m";else->"1d"}
            val range=when(timeframe){"15 min"->"60d";"1 hour"->"730d";else->"1y"}
            val url="https://query1.finance.yahoo.com/v8/finance/chart/"+symbol+".NS?interval="+interval+"&range="+range+"&events=history"
            val req=Request.Builder().url(url).header("User-Agent","Mozilla/5.0").build()
            val body=http.newCall(req).execute().use{it.body?.string() ?: return@withContext null}
            val result=JSONObject(body).getJSONObject("chart").getJSONArray("result").optJSONObject(0) ?: return@withContext null
            val q=result.getJSONObject("indicators").getJSONArray("quote").getJSONObject(0)
            val close=series(q,"close");val high=series(q,"high");val low=series(q,"low");val vol=series(q,"volume")
            if(close.size<60||high.size!=close.size||low.size!=close.size)return@withContext null
            val c=close.last();val e20=ema(close,20);val e50=ema(close,50);val r=rsi(close,14);val a=adx(high,low,close,14)
            val rv=if(vol.size>=21)vol.last()/(vol.takeLast(20).average().takeIf{it>0}?:1.0)else 0.0
            val vw=vwap(high,low,close,vol);val mac=ema(close,12)-ema(close,26);val sig=macdSignal(close);val st=supertrend(high,low,close,10,3.0)
            val trend=listOf(c>e20&&e20>e50,st,mac>sig).count{it};val part=listOf(c>vw,rv>=1.2).count{it}
            val score=(trend/3.0*40.0)+(if(r>50)20.0 else 0.0)+(part/2.0*20.0)+minOf(a/40.0,1.0)*10.0
            val qualifies=trend>=2&&r>50&&part>=1&&a>=20
            StockRow(symbol,c,score.toInt(),r,a,rv,if(st)"BULLISH"else"BEARISH",if(qualifies&&score>=70)"BUY"else"WATCH")
        }.getOrNull()
    }
    suspend fun checkAndroidUpdate(manifestUrl:String,currentVersion:String):CheckResult=withContext(Dispatchers.IO){
        val req=Request.Builder().url(manifestUrl).header("User-Agent","NSE-Bullish-Scanner-Android").build()
        val body=http.newCall(req).execute().use{it.body?.string() ?: throw IllegalStateException("Empty update manifest")}
        val j=JSONObject(body);val latest=j.optString("android_version",currentVersion);val url=j.optString("android_url","")
        val a=latest.split(".").map{it.toIntOrNull()?:0};val b=currentVersion.split(".").map{it.toIntOrNull()?:0}
        var newer=false
        for(i in 0 until maxOf(a.size,b.size)){val x=a.getOrElse(i){0};val y=b.getOrElse(i){0};if(x>y){newer=true;break};if(x<y)break}
        if(newer && url.isNotBlank()) CheckResult(true,"Update available: v"+latest,url) else CheckResult(false,"You are using the latest Android version.",null)
    }

    private fun series(q:JSONObject,key:String)=buildList<Double>{val a=q.optJSONArray(key)?:return@buildList;for(i in 0 until a.length())if(!a.isNull(i))add(a.getDouble(i))}
    private fun ema(x:List<Double>,n:Int):Double{var e=x.first();val k=2.0/(n+1);for(i in 1 until x.size)e=x[i]*k+e*(1-k);return e}
    private fun rsi(x:List<Double>,n:Int):Double{if(x.size<n+1)return 0.0;var ag=0.0;var al=0.0;for(i in 1..n){val d=x[i]-x[i-1];if(d>=0)ag+=d else al-=d};ag/=n;al/=n;for(i in n+1 until x.size){val d=x[i]-x[i-1];ag=(ag*(n-1)+(if(d>0)d else 0.0))/n;al=(al*(n-1)+(if(d<0)-d else 0.0))/n};return if(al==0.0)100.0 else 100-100/(1+ag/al)}
    private fun adx(h:List<Double>,l:List<Double>,c:List<Double>,n:Int):Double{if(c.size<n*2)return 0.0;var at=0.0;var p=0.0;var m=0.0;var dx=0.0;for(i in 1 until c.size){val tr=maxOf(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]));val up=h[i]-h[i-1];val dn=l[i-1]-l[i];at=if(i==1)tr else(at*(n-1)+tr)/n;p=if(up>dn&&up>0)(p*(n-1)+up)/n else p*(n-1)/n;m=if(dn>up&&dn>0)(m*(n-1)+dn)/n else m*(n-1)/n;val pi=if(at>0)100*p/at else 0.0;val mi=if(at>0)100*m/at else 0.0;val d=if(pi+mi>0)100*abs(pi-mi)/(pi+mi)else 0.0;dx=if(i<n)dx else(dx*(n-1)+d)/n};return dx}
    private fun vwap(h:List<Double>,l:List<Double>,c:List<Double>,v:List<Double>):Double{var pv=0.0;var vv=0.0;for(i in c.indices){val x=if(i<v.size)v[i]else 0.0;pv+=(h[i]+l[i]+c[i])/3*x;vv+=x};return if(vv>0)pv/vv else c.last()}
    private fun macdSignal(x:List<Double>):Double{var e12=x.first();var e26=x.first();var s=x.first();val k12=2.0/13;val k26=2.0/27;val ks=2.0/10;for(i in x.indices){if(i>0){e12=x[i]*k12+e12*(1-k12);e26=x[i]*k26+e26*(1-k26)};val m=e12-e26;s=m*ks+s*(1-ks)};return s}
    private fun supertrend(h:List<Double>,l:List<Double>,c:List<Double>,p:Int,m:Double):Boolean{var fu=0.0;var fl=0.0;var dir=true;var at=0.0;for(i in 1 until c.size){val tr=maxOf(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]));at=(at*(p-1)+tr)/p;val mid=(h[i]+l[i])/2;val up=mid+m*at;val lo=mid-m*at;if(i==1){fu=up;fl=lo}else{fu=if(up<fu||c[i-1]>fu)up else fu;fl=if(lo>fl||c[i-1]<fl)lo else fl};dir=if(dir)c[i]>=fl else c[i]>fu};return dir}
}
