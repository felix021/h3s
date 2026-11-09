package cn.felix021.h3squery;

import java.util.*;

public final class BlePacketizer {
    public static List<byte[]> split(byte[] frame,int attMtu) {
        if(frame==null || attMtu<23 || attMtu>517) throw new IllegalArgumentException("Invalid BLE MTU/frame");
        List<byte[]> out=new ArrayList<>(); int payload=attMtu-3;
        for(int p=0;p<frame.length;p+=payload) out.add(Arrays.copyOfRange(frame,p,Math.min(frame.length,p+payload)));
        return out;
    }
}
