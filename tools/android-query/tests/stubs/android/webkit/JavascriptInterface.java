package android.webkit;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * JVM 单测桩：模拟 android.webkit.JavascriptInterface 注解（真注解在 android.jar，仅 Android 构建可用）。
 * 保留策略与真注解一致（RUNTIME，WebView 按运行时反射发现方法），方法签名/目标相同，
 * 使 H3SBridge 源码无需分支即可在纯 JVM 下编译测试。
 */
@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.METHOD)
public @interface JavascriptInterface {
}
