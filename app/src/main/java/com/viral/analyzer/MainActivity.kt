package com.viral.analyzer

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.MediaStore
import android.view.View
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import kotlinx.coroutines.*
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream

class MainActivity : AppCompatActivity() {

    companion object {
        private const val PICK_VIDEO = 1001
        private const val SERVER_URL = BuildConfig.SERVER_URL
    }

    private lateinit var btnPick: Button
    private lateinit var btnAnalyze: Button
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

    private var selectedVideoUri: Uri? = null
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, java.util.concurrent.TimeUnit.SECONDS)
        .readTimeout(300, java.util.concurrent.TimeUnit.SECONDS)
        .writeTimeout(120, java.util.concurrent.TimeUnit.SECONDS)
        .build()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        btnPick = findViewById(R.id.btnPick)
        btnAnalyze = findViewById(R.id.btnAnalyze)
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

        btnPick.setOnClickListener { pickVideo() }
        btnAnalyze.setOnClickListener { analyzeVideo() }
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
        progressBar.visibility = View.VISIBLE
        layoutResult.visibility = View.GONE
        tvStatus.text = "⏳ جارٍ التحليل... (قد يستغرق دقيقة)"

        CoroutineScope(Dispatchers.IO).launch {
            try {
                // Copy video to temp file
                val tmpFile = File(cacheDir, "upload_video.mp4")
                contentResolver.openInputStream(uri)?.use { input ->
                    FileOutputStream(tmpFile).use { output ->
                        input.copyTo(output)
                    }
                }

                val requestBody = MultipartBody.Builder()
                    .setType(MultipartBody.FORM)
                    .addFormDataPart(
                        "video", "video.mp4",
                        tmpFile.asRequestBody("video/mp4".toMediaType())
                    )
                    .addFormDataPart("niche", niche)
                    .build()

                val request = Request.Builder()
                    .url("$SERVER_URL/analyze")
                    .post(requestBody)
                    .build()

                val response = client.newCall(request).execute()
                val body = response.body?.string() ?: throw Exception("لا يوجد رد")

                if (!response.isSuccessful) throw Exception("خطأ في الخادم: ${response.code}")

                val json = JSONObject(body)
                tmpFile.delete()

                withContext(Dispatchers.Main) {
                    displayResult(json)
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

    private fun displayResult(json: JSONObject) {
        progressBar.visibility = View.GONE
        layoutResult.visibility = View.VISIBLE
        btnAnalyze.isEnabled = true

        val score = json.optInt("score", 0)
        tvScore.text = "$score / 100"
        tvScore.setTextColor(
            when {
                score >= 75 -> getColor(android.R.color.holo_green_dark)
                score >= 50 -> getColor(android.R.color.holo_orange_dark)
                else -> getColor(android.R.color.holo_red_dark)
            }
        )

        tvHook.text = json.optString("hook_rating", "-")
        tvDuration.text = "${json.optInt("duration")}ث • ${json.optString("duration_rating")}"
        tvBestTime.text = json.optString("best_time", "-")

        // Add trend match info to verdict
        val verdict = json.optString("verdict", "-")
        val trendMatch = json.optInt("trend_match", 0)
        val trendUpdated = json.optString("trend_updated", "")
        val videosAnalyzed = json.optInt("videos_analyzed", 0)
        tvVerdict.text = verdict
        if (trendMatch > 0) {
            tvStatus.text = "✅ تحليل مكتمل • توافق مع الترند: $trendMatch% • بيانات من $videosAnalyzed فيديو ($trendUpdated)"
        }

        tvCaption.text = json.optString("caption", "-")
        tvHashtags.text = json.optString("hashtags", "-")
        tvTranscript.text = json.optString("transcript", "-")

        val strengths = json.optJSONArray("strengths") ?: JSONArray()
        tvStrengths.text = buildString {
            for (i in 0 until strengths.length()) {
                append("✅ ${strengths.getString(i)}\n")
            }
        }.trim()

        val improvements = json.optJSONArray("improvements") ?: JSONArray()
        tvImprovements.text = buildString {
            for (i in 0 until improvements.length()) {
                append("⚠️ ${improvements.getString(i)}\n")
            }
        }.trim()

        tvStatus.text = "✅ اكتمل التحليل"
    }
}
