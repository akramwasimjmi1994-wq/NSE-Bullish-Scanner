package com.akramwasim.nsebullishscanner
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.launch
import java.util.Locale

private val Bg=Color(0xFF0D1117);private val Header=Color(0xFF131722);private val Card=Color(0xFF1E222D)
private val Green=Color(0xFF26A69A);private val Muted=Color(0xFF787B86);private val Red=Color(0xFFEF5350)
const val APP_VERSION="1.0.1"
const val UPDATE_MANIFEST="https://raw.githubusercontent.com/akramwasimjmi1994-wq/NSE-Bullish-Scanner/main/update.json"
data class StockRow(val symbol:String,val price:Double,val score:Int,val rsi:Double,val adx:Double,val relVol:Double,val trend:String,val signal:String)
data class UiState(val running:Boolean=false,val done:Int=0,val total:Int=0,val rows:List<StockRow> = emptyList(),val error:String?=null)
data class CheckState(val running:Boolean=false,val message:String="",val url:String?=null)
class ScannerVm:ViewModel(){
 var state by mutableStateOf(UiState());private set
 var universe by mutableStateOf("Nifty 50");var timeframe by mutableStateOf("1 day");var minimum by mutableIntStateOf(70)
 var update by mutableStateOf(CheckState());private set
 private val client=YahooClient()
 fun scan(){if(state.running)return;val syms=Universe.symbols(universe);state=UiState(true,0,syms.size)
  viewModelScope.launch{val out=mutableListOf<StockRow>();for(batch in syms.chunked(8)){val rs=batch.map{s->async(Dispatchers.IO){client.scan(s,timeframe)}}.awaitAll();rs.filterNotNull().forEach{row->if(row.score>=minimum)out.add(row)};state=state.copy(done=minOf(state.done+batch.size,syms.size),rows=out.sortedByDescending{row->row.score})};state=state.copy(running=false)}}
 suspend fun checkOne(symbol:String,tf:String)=client.scan(symbol,tf)
 fun checkUpdate(){if(update.running)return;update=CheckState(true,"Checking for updates…");viewModelScope.launch(Dispatchers.IO){val r=runCatching{client.checkAndroidUpdate(UPDATE_MANIFEST,APP_VERSION)}.getOrElse{e->CheckResult(false,"Update check failed: "+e.message,null)};update=CheckState(false,r.message,r.url)}}
}
class MainActivity:ComponentActivity(){override fun onCreate(b:Bundle?){super.onCreate(b);setContent{App()}}}
@Composable fun App(){val vm=remember{ScannerVm()};val s=vm.state;val u=vm.update;val ctx=LocalContext.current;var tab by remember{mutableIntStateOf(0)}
 MaterialTheme(colorScheme=darkColorScheme(primary=Green,background=Bg,surface=Card,onSurface=Color(0xFFD1D4DC))){Surface(Modifier.fillMaxSize(),color=Bg){Column{
  Row(Modifier.fillMaxWidth().background(Header).padding(12.dp),verticalAlignment=Alignment.CenterVertically){Text("NSE",color=Green,fontWeight=FontWeight.Bold);Spacer(Modifier.width(6.dp));Text("BULLISH TERMINAL",fontWeight=FontWeight.Bold);Spacer(Modifier.weight(1f));TextButton(onClick={vm.checkUpdate()},enabled=!u.running){Text("UPDATE",color=Green,fontSize=11.sp)};Text("v"+APP_VERSION,color=Muted,fontSize=10.sp)}
  if(u.message.isNotBlank()){Row(Modifier.fillMaxWidth().background(Card).padding(6.dp),verticalAlignment=Alignment.CenterVertically){Text(u.message,color=if(u.url!=null)Green else Muted,fontSize=11.sp,modifier=Modifier.weight(1f));if(u.url!=null)TextButton(onClick={ctx.startActivity(Intent(Intent.ACTION_VIEW,Uri.parse(u.url)))}){Text("DOWNLOAD",color=Green,fontSize=10.sp)}}}
  TabRow(selectedTabIndex=tab,containerColor=Header){Tab(tab==0,{tab=0},text={Text("MARKET SCANNER")});Tab(tab==1,{tab=1},text={Text("STOCK CHECK")})}
  if(tab==0)ScannerScreen(vm,s) else StockCheckScreen(vm)
 }}}
}
@Composable fun ScannerScreen(vm:ScannerVm,s:UiState){Column(Modifier.padding(12.dp)){
 Row(horizontalArrangement=Arrangement.spacedBy(8.dp),modifier=Modifier.fillMaxWidth()){Choice(vm.universe,listOf("Nifty 50","Nifty 100","Nifty 200"),{v->vm.universe=v},Modifier.weight(1f));Choice(vm.timeframe,listOf("15 min","1 hour","1 day"),{v->vm.timeframe=v},Modifier.weight(1f))}
 Spacer(Modifier.height(8.dp));Row(verticalAlignment=Alignment.CenterVertically){Text("MIN SCORE",color=Muted,fontSize=11.sp);Slider(value=vm.minimum.toFloat(),onValueChange={v->vm.minimum=(v/5).toInt()*5},valueRange=50f..100f,steps=9,modifier=Modifier.weight(1f));Text(vm.minimum.toString()+"/100",fontWeight=FontWeight.Bold)}
 Button(onClick={vm.scan()},enabled=!s.running,modifier=Modifier.fillMaxWidth(),colors=ButtonDefaults.buttonColors(containerColor=Green),shape=RoundedCornerShape(8.dp)){Text(if(s.running)"SCANNING…" else "⚡ SCAN MARKET")}
 Spacer(Modifier.height(8.dp));Text(if(s.total>0)s.done.toString()+" / "+s.total+" stocks scanned" else "READY — press Scan Market",color=Muted)
 if(s.running)LinearProgressIndicator(progress={if(s.total==0)0f else s.done.toFloat()/s.total},modifier=Modifier.fillMaxWidth().padding(vertical=6.dp),color=Green)
 Row(horizontalArrangement=Arrangement.spacedBy(6.dp),modifier=Modifier.fillMaxWidth()){Kpi("SCANNED",s.done.toString(),Modifier.weight(1f));Kpi("BUY",s.rows.count{row->row.signal=="BUY"}.toString(),Modifier.weight(1f));Kpi("SHOWN",s.rows.size.toString(),Modifier.weight(1f))}}
 LazyColumn(Modifier.fillMaxSize().padding(horizontal=12.dp),verticalArrangement=Arrangement.spacedBy(6.dp)){items(s.rows,key={row->row.symbol}){row->StockCard(row)}}}
@Composable fun StockCheckScreen(vm:ScannerVm){var symbol by remember{mutableStateOf("")};var tf by remember{mutableStateOf("1 day")};var loading by remember{mutableStateOf(false)};var result by remember{mutableStateOf<StockRow?>(null)};var error by remember{mutableStateOf("")}
 Column(Modifier.padding(12.dp)){Text("CHECK PARTICULAR STOCK",fontWeight=FontWeight.Bold);Text("Runs the same bullish signal calculation used by the scanner.",color=Muted,fontSize=11.sp);Spacer(Modifier.height(8.dp))
 OutlinedTextField(value=symbol,onValueChange={v->symbol=v.uppercase(Locale.US).replace(".NS","")},singleLine=true,label={Text("NSE Symbol")},placeholder={Text("e.g. RELIANCE")},modifier=Modifier.fillMaxWidth());Spacer(Modifier.height(8.dp))
 Choice(tf,listOf("15 min","1 hour","1 day"),{v->tf=v},Modifier.fillMaxWidth());Spacer(Modifier.height(8.dp))
 Button(enabled=!loading&&symbol.isNotBlank(),onClick={loading=true;result=null;error="";vm.viewModelScope.launch{val r=vm.checkOne(symbol.trim(),tf);loading=false;if(r==null)error="Unable to fetch market data." else result=r}},modifier=Modifier.fillMaxWidth(),colors=ButtonDefaults.buttonColors(containerColor=Green)){Text(if(loading)"CHECKING…" else "CHECK BUY SIGNAL")}
 if(error.isNotBlank())Text(error,color=Red,modifier=Modifier.padding(top=8.dp));result?.let{r->StockCheckResult(r)}}}
@Composable fun StockCheckResult(r:StockRow){Spacer(Modifier.height(12.dp));Card(colors=CardDefaults.cardColors(containerColor=Header),modifier=Modifier.fillMaxWidth()){Column(Modifier.padding(14.dp)){
 Row(verticalAlignment=Alignment.CenterVertically){Text(r.symbol,fontWeight=FontWeight.Bold,fontSize=20.sp);Spacer(Modifier.weight(1f));Text(r.signal,color=if(r.signal=="BUY")Green else Red,fontWeight=FontWeight.Bold,fontSize=18.sp)}
 Text("Composite score: "+r.score+"/100",color=Muted);Spacer(Modifier.height(10.dp));Row(horizontalArrangement=Arrangement.spacedBy(14.dp)){Metric("Price",String.format(Locale.US,"₹%.2f",r.price));Metric("RSI",String.format(Locale.US,"%.1f",r.rsi));Metric("ADX",String.format(Locale.US,"%.1f",r.adx));Metric("RelVol",String.format(Locale.US,"%.2f",r.relVol))}
 Text("Trend: "+r.trend,color=if(r.trend=="BULLISH")Green else Red,fontSize=12.sp,modifier=Modifier.padding(top=8.dp))}}}
@Composable fun Choice(value:String,items:List<String>,onPick:(String)->Unit,modifier:Modifier=Modifier){var open by remember{mutableStateOf(false)};Box(modifier){OutlinedButton(onClick={open=true},modifier=Modifier.fillMaxWidth()){Text(value,fontSize=12.sp)};DropdownMenu(expanded=open,onDismissRequest={open=false}){items.forEach{item->DropdownMenuItem(text={Text(item)},onClick={onPick(item);open=false})}}}}
@Composable fun Kpi(title:String,value:String,modifier:Modifier){Card(colors=CardDefaults.cardColors(containerColor=Card),modifier=modifier){Column(Modifier.padding(8.dp)){Text(title,color=Muted,fontSize=10.sp);Text(value,fontWeight=FontWeight.Bold)}}}
@Composable fun StockCard(r:StockRow){Card(colors=CardDefaults.cardColors(containerColor=Header),modifier=Modifier.fillMaxWidth()){Column(Modifier.padding(12.dp)){Row(verticalAlignment=Alignment.CenterVertically){Text(r.symbol,fontWeight=FontWeight.Bold);Spacer(Modifier.weight(1f));Text(r.signal,color=if(r.signal=="BUY")Green else Muted,fontWeight=FontWeight.Bold);Text(" "+r.score+"/100")};Row(horizontalArrangement=Arrangement.spacedBy(12.dp)){Metric("Price",String.format(Locale.US,"₹%.2f",r.price));Metric("RSI",String.format(Locale.US,"%.1f",r.rsi));Metric("ADX",String.format(Locale.US,"%.1f",r.adx));Metric("RelVol",String.format(Locale.US,"%.2f",r.relVol))};Text(r.trend,color=if(r.trend=="BULLISH")Green else Red,fontSize=11.sp)}}}
@Composable fun Metric(a:String,b:String){Column{Text(a,color=Muted,fontSize=9.sp);Text(b,fontSize=12.sp,fontWeight=FontWeight.SemiBold)}}