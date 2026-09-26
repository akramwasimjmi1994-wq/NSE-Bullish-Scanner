package com.akramwasim.nsebullishscanner

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

data class StockRow(val symbol:String,val price:Double,val score:Int,val rsi:Double,val adx:Double,val relVol:Double,val trend:String,val signal:String)
data class UiState(val running:Boolean=false,val done:Int=0,val total:Int=0,val rows:List<StockRow> = emptyList(),val error:String?=null)

class ScannerVm:ViewModel(){
    var state by mutableStateOf(UiState());private set
    var universe by mutableStateOf("Nifty 50");var timeframe by mutableStateOf("1 day");var minimum by mutableIntStateOf(70)
    private val client=YahooClient()
    fun scan(){
        if(state.running)return
        val syms=Universe.symbols(universe);state=UiState(true,0,syms.size,emptyList(),null)
        viewModelScope.launch{
            val out=mutableListOf<StockRow>()
            for(batch in syms.chunked(8)){
                val results=batch.map{s->async(Dispatchers.IO){client.scan(s,timeframe)}}.awaitAll()
                results.filterNotNull().forEach{if(it.score>=minimum)out.add(it)}
                state=state.copy(done=minOf(state.done+batch.size,syms.size),rows=out.sortedByDescending{it.score})
            }
            state=state.copy(running=false)
        }
    }
}

class MainActivity:ComponentActivity(){override fun onCreate(savedInstanceState:Bundle?){super.onCreate(savedInstanceState);setContent{App()}}}

@Composable fun App(){
    val vm=remember{ScannerVm()};val s=vm.state
    MaterialTheme(colorScheme=darkColorScheme(primary=Green,background=Bg,surface=Card,onBackground=Color(0xFFD1D4DC),onSurface=Color(0xFFD1D4DC))){
        Surface(Modifier.fillMaxSize(),color=Bg){
            Column{
                Row(Modifier.fillMaxWidth().background(Header).padding(16.dp),verticalAlignment=Alignment.CenterVertically){
                    Text("NSE",color=Green,fontWeight=FontWeight.Bold);Spacer(Modifier.width(6.dp));Text("BULLISH TERMINAL",fontWeight=FontWeight.Bold)
                    Spacer(Modifier.weight(1f));Text("Android v1.0",color=Muted,fontSize=11.sp)
                }
                Column(Modifier.padding(12.dp)){
                    Row(horizontalArrangement=Arrangement.spacedBy(8.dp),modifier=Modifier.fillMaxWidth()){
                        Choice(vm.universe,listOf("Nifty 50","Nifty 100","Nifty 200"),{vm.universe=it},Modifier.weight(1f))
                        Choice(vm.timeframe,listOf("15 min","1 hour","1 day"),{vm.timeframe=it},Modifier.weight(1f))
                    }
                    Spacer(Modifier.height(8.dp))
                    Row(verticalAlignment=Alignment.CenterVertically){
                        Text("MIN SCORE",color=Muted,fontSize=11.sp);Spacer(Modifier.width(8.dp))
                        Slider(value=vm.minimum.toFloat(),onValueChange={vm.minimum=(it/5).toInt()*5},valueRange=50f..100f,steps=9,modifier=Modifier.weight(1f))
                        Text(vm.minimum.toString()+"/100",fontWeight=FontWeight.Bold)
                    }
                    Button(onClick={vm.scan()},enabled=!s.running,modifier=Modifier.fillMaxWidth(),colors=ButtonDefaults.buttonColors(containerColor=Green),shape=RoundedCornerShape(8.dp)){Text(if(s.running)"SCANNING…" else "⚡ SCAN MARKET")}
                    Spacer(Modifier.height(10.dp))
                    Card(colors=CardDefaults.cardColors(containerColor=Header),modifier=Modifier.fillMaxWidth()){
                        Column(Modifier.padding(12.dp)){
                            Text(if(s.total>0)s.done.toString()+" / "+s.total+" stocks scanned" else "READY — press Scan Market",color=Muted)
                            if(s.running)LinearProgressIndicator(progress={if(s.total==0)0f else s.done.toFloat()/s.total},modifier=Modifier.fillMaxWidth().padding(top=8.dp),color=Green)
                        }
                    }
                    Spacer(Modifier.height(10.dp))
                    Row(horizontalArrangement=Arrangement.spacedBy(6.dp),modifier=Modifier.fillMaxWidth()){
                        Kpi("SCANNED",s.done.toString(),Modifier.weight(1f));Kpi("BULLISH",s.rows.count{it.signal=="BUY"}.toString(),Modifier.weight(1f));Kpi("SHOWN",s.rows.size.toString(),Modifier.weight(1f))
                    }
                    if(s.error!=null)Text("Data warning: "+s.error,color=Red,modifier=Modifier.padding(vertical=6.dp))
                }
                LazyColumn(Modifier.fillMaxSize().padding(horizontal=12.dp),verticalArrangement=Arrangement.spacedBy(6.dp)){
                    items(s.rows,key={it.symbol}){r->StockCard(r)}
                }
            }
        }
    }
}
@Composable fun Choice(value:String,items:List<String>,onPick:(String)->Unit,modifier:Modifier=Modifier){
    var open by remember{mutableStateOf(false)}
    Box(modifier){
        OutlinedButton(onClick={open=true},modifier=Modifier.fillMaxWidth()){Text(value,fontSize=12.sp)}
        DropdownMenu(expanded=open,onDismissRequest={open=false}){items.forEach{DropdownMenuItem(text={Text(it)},onClick={onPick(it);open=false})}}
    }
}
@Composable fun Kpi(title:String,value:String,modifier:Modifier){Card(colors=CardDefaults.cardColors(containerColor=Card),modifier=modifier){Column(Modifier.padding(9.dp)){Text(title,color=Muted,fontSize=10.sp);Text(value,fontWeight=FontWeight.Bold)}}}
@Composable fun StockCard(r:StockRow){
    Card(colors=CardDefaults.cardColors(containerColor=Header),modifier=Modifier.fillMaxWidth()){
        Column(Modifier.padding(12.dp)){
            Row(verticalAlignment=Alignment.CenterVertically){
                Text(r.symbol,fontWeight=FontWeight.Bold,fontSize=15.sp);Spacer(Modifier.weight(1f))
                Text(r.signal,color=if(r.signal=="BUY")Green else Muted,fontWeight=FontWeight.Bold)
                Spacer(Modifier.width(10.dp));Text(r.score.toString()+"/100",color=if(r.score>=70)Green else Color.White,fontWeight=FontWeight.Bold)
            }
            Spacer(Modifier.height(6.dp))
            Row(horizontalArrangement=Arrangement.spacedBy(12.dp)){
                Metric("Price",String.format(Locale.US,"₹%.2f",r.price));Metric("RSI",String.format(Locale.US,"%.1f",r.rsi))
                Metric("ADX",String.format(Locale.US,"%.1f",r.adx));Metric("RelVol",String.format(Locale.US,"%.2f",r.relVol))
            }
            Text(r.trend,color=if(r.trend=="BULLISH")Green else Red,fontSize=11.sp,modifier=Modifier.padding(top=6.dp))
        }
    }
}
@Composable fun Metric(a:String,b:String){Column{Text(a,color=Muted,fontSize=9.sp);Text(b,fontSize=12.sp,fontWeight=FontWeight.SemiBold)}}
