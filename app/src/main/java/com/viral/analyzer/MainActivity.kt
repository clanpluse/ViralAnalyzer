package com.viral.analyzer

import android.Manifest
import android.app.Activity
import android.content.ContentValues
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.provider.Settings
import android.text.SpannableString
import android.text.Spanned
import android.text.style.AbsoluteSizeSpan
import android.text.style.BackgroundColorSpan
import android.text.style.ForegroundColorSpan
import android.view.View
import android.widget.*
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.media3.common.MediaItem
import androidx.media3.common.util.UnstableApi
import androidx.media3.effect.OverlayEffect
import androidx.media3.effect.OverlaySettings
import androidx.media3.effect.StaticOverlaySettings
import androidx.media3.effect.TextOverlay
import androidx.media3.effect.TextureOverlay
import androidx.media3.transformer.Composition
import androidx.media3.transformer.EditedMediaItem
import androidx.media3.transformer.Effects
import androidx.media3.transformer.ExportException
import androidx.media3.transformer.ExportResult
import androidx.media3.transformer.Transformer
import com.google.common.collect.ImmutableList
import kotlinx.coroutines.*
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import kotlin.coroutines.resume
import kotlin.coroutines.suspendCoroutine

class MainActivity : AppCompatActivity() {

    companion object {
        private const val PICK_VIDEO = 1001
        private const val SERVER_URL = BuildConfig.SERVER_URL
    }

    private lateinit var btnPick: Button
    private lateinit var btnAnalyze: Button
    private lateinit var btnEnhance: Button
    private lateinit var btnReport: Button
    private lateinit var tvStatus: TextView
    private lateinit var spinnerNiche: Spinner
    private lateinit var progressBar: ProgressBar
    private lateinit var layoutResult: ScrollView
    private lateinit var tvScore: TextView
    private lateinit var tvHook: TextView
    private lateinit var tvDuration: TextView
    private lateinit var tvStrengths: TextView
    private lateinit var tvImprovements: TextView
    private lateinit var tvCaption: TextView
    private lateinit var tvHashtags: TextView
    private lateinit var tvBestTime: TextView
    private lateinit var tvVerdict: TextView
    private lateinit var tvTranscript: TextView
    private lateinit var tvVideoName: TextView
    private lateinit var btnCopyAll: Button

    private var selectedVideoUri: Uri? = null
    private var lastAnalysisResult: JSONObject? = null
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, java.util.concurrent.TimeUnit.SECONDS)
        .readTimeout(300, java.util.concurrent.TimeUnit.SECONDS)
        .writeTimeout(120, java.util.concurrent.TimeUnit.SECONDS)
        .build()

    private val requestPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        btnPick = findViewById(R.id.btnPick)
        btnAnalyze = findViewById(R.id.btnAnalyze)
        btnEnhance = findViewById(R.id.btnEnhance)
        btnReport = findViewById(R.id.btnReport)
        tvStatus = findViewById(R.id.tvStatus)
        spinnerNiche = findViewById(R.id.spinnerNiche)
        progressBar = findViewById(R.id.progressBar)
        layoutResult = findViewById(R.id.layoutResult)
        tvScore = findViewById(R.id.tvScore)
        tvHook = findViewById(R.id.tvHook)
        tvDuration = findViewById(R.id.tvDuration)
        tvStrengths = findViewById(R.id.tvStrengths)
        tvImprovements = findViewById(R.id.tvImprovements)
        tvCaption = findViewById(R.id.tvCaption)
        tvHashtags = findViewById(R.id.tvHashtags)
        tvBestTime = findViewById(R.id.tvBestTime)
        tvVerdict = findViewById(R.id.tvVerdict)
        tvTranscript = findViewById(R.id.tvTranscript)
        tvVideoName = findViewById(R.id.tvVideoName)
        btnCopyAll = findViewById(R.id.btnCopyAll)

        val niches = listOf(
            "تصاميم منزلية وديكور",
            "تسويق منتجات",
            "أزياء وموضة",
            "طعام ومطاعم",
            "تقنية وإلكترونيات",
            "رياضة ولياقة",
            "سفر وسياحة",
            "عام"
        )
        spinnerNiche.adapter = ArrayAdapter(
            this, android.R.layout.simple_spinner_dropdown_item, niches
        )

        requestPermissions()

        btnPick.setOnClickListener { pickVideo() }
        btnAnalyze.setOnClickListener { analyzeVideo() }
        btnEnhance.setOnClickListener { enhanceVideo() }
        btnReport.setOnClickListener { showTrendReport() }
        btnCopyAll.setOnClickListener { copyAllToClipboard() }
    }

    private fun requestPermissions() {
        val perms = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            perms.add(Manifest.permission.READ_MEDIA_VIDEO)
            perms.add(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            perms.add(Manifest.permission.READ_EXTERNAL_STORAGE)
            perms.add(Manifest.permission.WRITE_EXTERNAL_STORAGE)
        }
        requestPermissionLauncher.launch(perms.toTypedArray())
    }

    private fun pickVideo() {
        val intent = Intent(Intent.ACTION_PICK, MediaStore.Video.Media.EXTERNAL_CONTENT_URI)
        startActivityForResult(intent, PICK_VIDEO)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == PICK_VIDEO && resultCode == Activity.RESULT_OK) {
            selectedVideoUri = data?.data
            selectedVideoUri?.let { uri ->
                val name = getFileName(uri)
                tvVideoName.text = "📹 $name"
                tvVideoName.visibility = View.VISIBLE
                btnAnalyze.isEnabled = true
                tvStatus.text = "جاهز للتحليل"
                layoutResult.visibility = View.GONE
                btnEnhance.visibility = View.GONE
            }
        }
    }

    private fun getFileName(uri: Uri): String {
        val cursor = contentResolver.query(uri, null, null, null, null)
        return cursor?.use {
            val nameIndex = it.getColumnIndex(MediaStore.Video.Media.DISPLAY_NAME)
            it.moveToFirst()
            it.getString(nameIndex)
        } ?: "فيديو"
    }

    private fun analyzeVideo() {
        val uri = selectedVideoUri ?: return
        val niche = spinnerNiche.selectedItem.toString()

        btnAnalyze.isEnabled = false
        btnEnhance.visibility = View.GONE
        progressBar.visibility = View.VISIBLE
        layoutResult.visibility = View.GONE
        tvStatus.text = "⏳ جارٍ التحليل... (قد يستغرق دقيقة)"

        CoroutineScope(Dispatchers.IO).launch {
            try {
                val tmpFile = File(cacheDir, "upload_video.mp4")
                contentResolver.openInputStream(uri)?.use { input ->
                    FileOutputStream(tmpFile).use { output -> input.copyTo(output) }
                }

                val requestBody = MultipartBody.Builder()
                    .setType(MultipartBody.FORM)
                    .addFormDataPart("video", "video.mp4",
                        tmpFile.asRequestBody("video/mp4".toMediaType()))
                    .addFormDataPart("niche", niche)
                    .build()

                val request = Request.Builder()
                    .url("$SERVER_URL/analyze")
                    .post(requestBody)
                    .build()

                val response = client.newCall(request).execute()
                val body = response.body?.string() ?: throw Exception("لا يوجد رد")
                if (!response.isSuccessful) throw Exception("خطأ: ${response.code}")

                val json = JSONObject(body)
                lastAnalysisResult = json
                tmpFile.delete()

                withContext(Dispatchers.Main) {
                    displayResult(json)
                    btnEnhance.visibility = View.VISIBLE
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    tvStatus.text = "❌ خطأ: ${e.message}"
                    progressBar.visibility = View.GONE
                    btnAnalyze.isEnabled = true
                }
            }
        }
    }

    private fun enhanceVideo() {
        val uri = selectedVideoUri ?: return
        val niche = spinnerNiche.selectedItem.toString()
        val result = lastAnalysisResult ?: return

        btnEnhance.isEnabled = false
        progressBar.visibility = View.VISIBLE
        tvStatus.text = "🎬 جارٍ توليد النصوص..."

        CoroutineScope(Dispatchers.IO).launch {
            try {
                // 1) Ask the server only for the smart text (fast, lightweight JSON)
                val durationSec = result.optInt("duration", 0)
                val requestBody = MultipartBody.Builder()
                    .setType(MultipartBody.FORM)
                    .addFormDataPart("niche", niche)
                    .addFormDataPart("title", result.optString("caption", ""))
                    .addFormDataPart("transcript", result.optString("transcript", ""))
                    .addFormDataPart("duration", durationSec.toString())
                    .build()

                val request = Request.Builder()
                    .url("$SERVER_URL/enhance")
                    .post(requestBody)
                    .build()

                val resp = client.newCall(request).execute()
                val body = resp.body?.string() ?: throw Exception("لا يوجد رد")
                if (!resp.isSuccessful) throw Exception("خطأ: ${resp.code}")
                val json = JSONObject(body)

                val hookText = json.optString("hook_text", "")
                val hookReason = json.optString("hook_reason", "")
                val engageText = json.optString("engagement_text", "")
                val ctaText = json.optString("cta_text", "")
                val algoBoost = json.optString("algorithm_score_boost", "")

                // 2) Render the overlays ON THE DEVICE (fast, hardware accelerated)
                withContext(Dispatchers.Main) {
                    tvStatus.text = "🎬 جارٍ تحسين الفيديو على الجهاز..."
                }
                val outFile = File(cacheDir, "viral_enhanced_${System.currentTimeMillis()}.mp4")
                renderOverlaysOnDevice(uri, hookText, engageText, ctaText, durationSec, outFile)

                // 3) Save to gallery
                saveVideoFileToGallery(outFile)
                outFile.delete()

                withContext(Dispatchers.Main) {
                    progressBar.visibility = View.GONE
                    btnEnhance.isEnabled = true
                    tvStatus.text = "✅ الفيديو المحسّن تم حفظه في المعرض!"

                    val report = buildString {
                        appendLine("🎬 التحسينات المُطبَّقة على الفيديو:")
                        appendLine("━━━━━━━━━━━━━━━━━")
                        if (hookText.isNotEmpty()) {
                            appendLine("\n🎣 Hook (أول 3 ثواني):")
                            appendLine("  \"$hookText\"")
                            if (hookReason.isNotEmpty()) appendLine("  💡 $hookReason")
                        }
                        if (engageText.isNotEmpty()) {
                            appendLine("\n💬 نص التفاعل (المنتصف):")
                            appendLine("  \"$engageText\"")
                        }
                        if (ctaText.isNotEmpty()) {
                            appendLine("\n📢 نداء للعمل (النهاية):")
                            appendLine("  \"$ctaText\"")
                        }
                        if (algoBoost.isNotEmpty()) {
                            appendLine("\n🚀 تأثير على الخوارزمية:")
                            appendLine("  $algoBoost")
                        }
                    }

                    android.app.AlertDialog.Builder(this@MainActivity)
                        .setTitle("✅ الفيديو المحسّن جاهز!")
                        .setMessage(report)
                        .setPositiveButton("ممتاز!") { _, _ -> }
                        .show()
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    progressBar.visibility = View.GONE
                    btnEnhance.isEnabled = true
                    tvStatus.text = "❌ خطأ في التحسين: ${e.message}"
                }
            }
        }
    }

    /** Burn Hook/Engagement/CTA text onto the video locally using Media3 Transformer. */
    @OptIn(UnstableApi::class)
    private suspend fun renderOverlaysOnDevice(
        inputUri: Uri,
        hook: String,
        engage: String,
        cta: String,
        durationSec: Int,
        outFile: File
    ) = suspendCoroutine<Unit> { cont ->
        val durationUs = if (durationSec > 0) durationSec * 1_000_000L else 30_000_000L
        val hookEnd = minOf(3_000_000L, (durationUs * 0.2).toLong())
        val engStart = (durationUs * 0.4).toLong()
        val engEnd = (durationUs * 0.7).toLong()
        val ctaStart = if (durationUs > 3_000_000L) durationUs - 3_000_000L else (durationUs * 0.8).toLong()

        // One overlay per non-empty text, visible only inside its time window.
        val overlays = ArrayList<TextureOverlay>()
        if (hook.isNotEmpty())
            overlays.add(WindowedTextOverlay(hook, 0L, hookEnd, topAnchorY = 0.75f))
        if (engage.isNotEmpty())
            overlays.add(WindowedTextOverlay(engage, engStart, engEnd, topAnchorY = -0.75f))
        if (cta.isNotEmpty())
            overlays.add(WindowedTextOverlay(cta, ctaStart, durationUs, topAnchorY = 0.75f))

        if (overlays.isEmpty()) {
            // Nothing to draw — just copy the original through
            cont.resumeWith(Result.failure(Exception("لا توجد نصوص لإضافتها")))
            return@suspendCoroutine
        }

        val overlayEffect = OverlayEffect(ImmutableList.copyOf(overlays))

        val editedItem = EditedMediaItem.Builder(MediaItem.fromUri(inputUri))
            .setEffects(Effects(emptyList(), listOf(overlayEffect)))
            .build()

        val transformer = Transformer.Builder(this)
            .addListener(object : Transformer.Listener {
                override fun onCompleted(composition: Composition, result: ExportResult) {
                    cont.resume(Unit)
                }
                override fun onError(
                    composition: Composition,
                    result: ExportResult,
                    exception: ExportException
                ) {
                    cont.resumeWith(Result.failure(Exception("فشل المعالجة: ${exception.message}")))
                }
            })
            .build()

        // Transformer must be started on the main thread
        runOnUiThread {
            transformer.start(editedItem, outFile.absolutePath)
        }
    }

    private fun saveVideoFileToGallery(src: File) {
        val filename = "viral_enhanced_${System.currentTimeMillis()}.mp4"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val values = ContentValues().apply {
                put(MediaStore.Video.Media.DISPLAY_NAME, filename)
                put(MediaStore.Video.Media.MIME_TYPE, "video/mp4")
                put(MediaStore.Video.Media.RELATIVE_PATH, Environment.DIRECTORY_MOVIES)
            }
            val uri = contentResolver.insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values)
            uri?.let {
                contentResolver.openOutputStream(it)?.use { out ->
                    src.inputStream().use { input -> input.copyTo(out) }
                }
            }
        } else {
            val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_MOVIES)
            dir.mkdirs()
            src.copyTo(File(dir, filename), overwrite = true)
        }
    }

    private fun saveVideoToGallery(bytes: ByteArray) {
        val filename = "viral_enhanced_${System.currentTimeMillis()}.mp4"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val values = ContentValues().apply {
                put(MediaStore.Video.Media.DISPLAY_NAME, filename)
                put(MediaStore.Video.Media.MIME_TYPE, "video/mp4")
                put(MediaStore.Video.Media.RELATIVE_PATH, Environment.DIRECTORY_MOVIES)
            }
            val uri = contentResolver.insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values)
            uri?.let {
                contentResolver.openOutputStream(it)?.use { out -> out.write(bytes) }
            }
        } else {
            val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_MOVIES)
            dir.mkdirs()
            File(dir, filename).writeBytes(bytes)
        }
    }

    private fun showTrendReport() {
        btnReport.isEnabled = false
        tvStatus.text = "📊 جارٍ تحميل تقرير الترند..."

        CoroutineScope(Dispatchers.IO).launch {
            try {
                val request = Request.Builder()
                    .url("$SERVER_URL/trend-report")
                    .get()
                    .build()

                val response = client.newCall(request).execute()
                val body = response.body?.string() ?: ""

                withContext(Dispatchers.Main) {
                    btnReport.isEnabled = true
                    if (response.isSuccessful) {
                        showReportDialog(body)
                    } else {
                        tvStatus.text = "⏳ لم يتم إنشاء التقرير بعد، انتظر التحليل اليومي"
                    }
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    btnReport.isEnabled = true
                    tvStatus.text = "❌ خطأ: ${e.message}"
                }
            }
        }
    }

    private fun showReportDialog(reportJson: String) {
        try {
            val json = JSONObject(reportJson)
            val niches = json.optJSONObject("niches") ?: return
            val generatedAt = json.optString("generated_at", "").take(10)

            val sb = StringBuilder()
            sb.appendLine("📊 تقرير الترند - $generatedAt")
            sb.appendLine("━━━━━━━━━━━━━━━━━")

            niches.keys().forEach { niche ->
                val data = niches.getJSONObject(niche)
                sb.appendLine("\n🔥 $niche")
                sb.appendLine("📹 فيديوهات محللة: ${data.optInt("total_videos")}")
                sb.appendLine("⏱ المدة المثالية: ${data.optInt("optimal_duration")}ث")
                val hashtags = data.optJSONArray("top_hashtags")
                if (hashtags != null && hashtags.length() > 0) {
                    val tags = (0 until hashtags.length()).map { "#${hashtags.getString(it)}" }
                    sb.appendLine("# ${tags.joinToString(" ")}")
                }
                sb.appendLine("💡 ${data.optString("key_finding")}")

                val accounts = data.optJSONArray("accounts")
                if (accounts != null) {
                    sb.appendLine("الحسابات المدروسة:")
                    for (i in 0 until accounts.length()) {
                        val acc = accounts.getJSONObject(i)
                        sb.appendLine("  • @${acc.optString("username")} (${acc.optInt("videos_analyzed")} فيديو)")
                    }
                }
            }

            android.app.AlertDialog.Builder(this)
                .setTitle("📊 تقرير الترند")
                .setMessage(sb.toString())
                .setPositiveButton("نسخ") { _, _ ->
                    val clipboard = getSystemService(android.content.ClipboardManager::class.java)
                    clipboard.setPrimaryClip(android.content.ClipData.newPlainText("report", sb.toString()))
                    Toast.makeText(this, "تم النسخ", Toast.LENGTH_SHORT).show()
                }
                .setNegativeButton("إغلاق", null)
                .show()

            tvStatus.text = "✅ تقرير الترند"
        } catch (e: Exception) {
            tvStatus.text = "❌ خطأ في قراءة التقرير"
        }
    }

    private fun copyAllToClipboard() {
        val result = lastAnalysisResult ?: return
        val text = buildString {
            appendLine(result.optString("caption", ""))
            appendLine()
            appendLine(result.optString("hashtags", ""))
            appendLine()
            appendLine("⏰ أفضل وقت: ${result.optString("best_time", "")}")
        }
        val clipboard = getSystemService(android.content.ClipboardManager::class.java)
        clipboard.setPrimaryClip(android.content.ClipData.newPlainText("post", text))
        Toast.makeText(this, "✅ تم نسخ الكابشن والهاشتاقات", Toast.LENGTH_SHORT).show()
    }

    private fun displayResult(json: JSONObject) {
        progressBar.visibility = View.GONE
        layoutResult.visibility = View.VISIBLE
        btnAnalyze.isEnabled = true

        val score = json.optInt("score", 0)
        tvScore.text = "$score / 100"
        tvScore.setTextColor(when {
            score >= 75 -> getColor(android.R.color.holo_green_dark)
            score >= 50 -> getColor(android.R.color.holo_orange_dark)
            else -> getColor(android.R.color.holo_red_dark)
        })

        tvHook.text = json.optString("hook_rating", "-")
        tvDuration.text = "${json.optInt("duration")}ث • ${json.optString("duration_rating")}"
        tvBestTime.text = json.optString("best_time", "-")
        tvVerdict.text = json.optString("verdict", "-")
        tvCaption.text = json.optString("caption", "-")
        tvHashtags.text = json.optString("hashtags", "-")
        tvTranscript.text = json.optString("transcript", "-")

        val strengths = json.optJSONArray("strengths") ?: JSONArray()
        tvStrengths.text = buildString {
            for (i in 0 until strengths.length()) append("✅ ${strengths.getString(i)}\n")
        }.trim()

        val improvements = json.optJSONArray("improvements") ?: JSONArray()
        tvImprovements.text = buildString {
            for (i in 0 until improvements.length()) append("⚠️ ${improvements.getString(i)}\n")
        }.trim()

        val trendMatch = json.optInt("trend_match", 0)
        val trendUpdated = json.optString("trend_updated", "")
        tvStatus.text = if (trendMatch > 0)
            "✅ تحليل مكتمل • توافق مع الترند: $trendMatch% ($trendUpdated)"
        else "✅ تحليل مكتمل"
    }
}

/**
 * A text overlay shown only within [startUs, endUs]. Outside the window it stays
 * fully transparent. Positioned via [topAnchorY] in normalized coords:
 * +0.75 = near the top, -0.75 = near the bottom.
 */
@UnstableApi
class WindowedTextOverlay(
    text: String,
    private val startUs: Long,
    private val endUs: Long,
    private val topAnchorY: Float
) : TextOverlay() {

    private val span: SpannableString = SpannableString(text).apply {
        setSpan(AbsoluteSizeSpan(72), 0, length, Spanned.SPAN_INCLUSIVE_INCLUSIVE)
        setSpan(ForegroundColorSpan(Color.WHITE), 0, length, Spanned.SPAN_INCLUSIVE_INCLUSIVE)
        setSpan(BackgroundColorSpan(Color.argb(160, 0, 0, 0)), 0, length, Spanned.SPAN_INCLUSIVE_INCLUSIVE)
    }

    override fun getText(presentationTimeUs: Long): SpannableString = span

    override fun getOverlaySettings(presentationTimeUs: Long): OverlaySettings {
        val visible = presentationTimeUs in startUs..endUs
        return StaticOverlaySettings.Builder()
            .setAlphaScale(if (visible) 1f else 0f)
            .setBackgroundFrameAnchor(0f, topAnchorY)
            .setOverlayFrameAnchor(0f, 0f)
            .build()
    }
}
