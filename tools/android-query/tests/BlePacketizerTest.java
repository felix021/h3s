import cn.felix021.h3squery.BlePacketizer;
import java.io.ByteArrayOutputStream;
import java.util.*;

public class BlePacketizerTest {
    public static void main(String[] args) {
        for(int mtu:new int[]{23,64,247,517}) for(int size:new int[]{0,1,20,244,245,512,522,550,4096}) {
            byte[] frame=new byte[size]; for(int i=0;i<size;i++) frame[i]=(byte)(i^(i>>8));
            List<byte[]> packets=BlePacketizer.split(frame,mtu); ByteArrayOutputStream out=new ByteArrayOutputStream();
            for(byte[] packet:packets) {
                if(packet.length<1 || packet.length>mtu-3) throw new AssertionError("Payload overflow");
                out.write(packet,0,packet.length);
            }
            if(!Arrays.equals(frame,out.toByteArray()) || packets.size()!=(size+mtu-4)/(mtu-3)) throw new AssertionError("Loss/duplication");
        }
        for(int bad:new int[]{0,22,518}) {
            try { BlePacketizer.split(new byte[1],bad); throw new AssertionError("Invalid MTU accepted"); }
            catch(IllegalArgumentException expected) { }
        }
        if(BlePacketizer.split(new byte[522],247).size()!=3) throw new AssertionError("Expected 3 chunks");
        System.out.println("BlePacketizerTest passed: byte-exact reconstruction and ATT bounds, including 522-byte frame at MTU 247");
    }
}
