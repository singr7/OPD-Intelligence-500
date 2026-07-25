# kotlinx.serialization keeps its generated serializers by companion; R8 needs
# to be told, or every wire model deserialises to an exception on release.
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class ai.radpretation.opd.data.** {
    *** Companion;
}
-keepclasseswithmembers class ai.radpretation.opd.data.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class ai.radpretation.opd.data.**$$serializer { *; }

# OkHttp's optional platform integrations are absent by design.
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
