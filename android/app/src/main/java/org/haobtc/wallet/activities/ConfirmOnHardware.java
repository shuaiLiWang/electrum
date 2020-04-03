package org.haobtc.wallet.activities;

import android.app.Activity;
import android.app.Dialog;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.text.TextUtils;
import android.util.Log;
import android.view.Gravity;
import android.view.LayoutInflater;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.recyclerview.widget.RecyclerView;

import org.haobtc.wallet.R;
import org.haobtc.wallet.activities.base.BaseActivity;
import org.haobtc.wallet.activities.jointwallet.CommunicationModeSelector;
import org.haobtc.wallet.adapter.HardwareAdapter;
import org.haobtc.wallet.bean.GetnewcreatTrsactionListBean;
import org.haobtc.wallet.event.SendMoreAddressEvent;

import java.util.ArrayList;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

import butterknife.BindView;
import butterknife.ButterKnife;

public class ConfirmOnHardware extends BaseActivity implements View.OnClickListener {
    public static final String TAG = "org.haobtc.wallet.activities.ConfirmOnHardware";
    @BindView(R.id.linBitcoin)
    LinearLayout linBitcoin;
    @BindView(R.id.testConfirmMsg)
    TextView testConfirmMsg;
    private Dialog dialog;
    private View view;
    private ImageView imageViewCancel, imageViewSigning, imageBack;
    @BindView(R.id.img_back)
    ImageView imgBack;
    @BindView(R.id.tet_payAddress)
    TextView tetPayAddress;
    @BindView(R.id.tet_feeNum)
    TextView tetFeeNum;
    @BindView(R.id.recl_Msg)
    RecyclerView reclMsg;

    public int getLayoutId() {
        return R.layout.confirm_on_hardware;
    }

    @Override
    public void initView() {
        ButterKnife.bind(this);
        findViewById(R.id.confirm_on_hardware).setOnClickListener(this);
        findViewById(R.id.img_back).setOnClickListener(this);
    }

    @Override
    public void initData() {
        ArrayList<SendMoreAddressEvent> addressEventList = new ArrayList<>();
        Bundle bundle = getIntent().getBundleExtra("outputs");
        if (bundle != null) {
            String payAddress = bundle.getString("pay_address");
            String fee = bundle.getString("fee");
            ArrayList<GetnewcreatTrsactionListBean.OutputAddrBean> outputs = (ArrayList<GetnewcreatTrsactionListBean.OutputAddrBean>) bundle.getSerializable("output");
            for (GetnewcreatTrsactionListBean.OutputAddrBean output : outputs) {
                SendMoreAddressEvent sendMoreAddressEvent = new SendMoreAddressEvent();
                String addr = output.getAddr();
                String amount = output.getAmount();
                sendMoreAddressEvent.setInputAddress(addr);
                sendMoreAddressEvent.setInputAmount(amount);
                addressEventList.add(sendMoreAddressEvent);
            }
            Log.i("addressEventList", "-----: " + addressEventList);
            tetPayAddress.setText(payAddress);
            tetFeeNum.setText(fee);
            HardwareAdapter hardwareAdapter = new HardwareAdapter(addressEventList);
            reclMsg.setAdapter(hardwareAdapter);
        } else {
            linBitcoin.setVisibility(View.GONE);
            testConfirmMsg.setText(getString(R.string.confirm_hardware_msg));
        }
    }

    private void showPopupSignProcessing() {
        view = LayoutInflater.from(this).inflate(R.layout.touch_process_popupwindow, null);
        imageViewCancel = view.findViewById(R.id.cancel_touch);
        imageViewSigning = view.findViewById(R.id.imageView_signing);
        imageViewCancel.setOnClickListener(this);
        dialog = new Dialog(this, R.style.dialog);
        dialog.setContentView(view);
        Window window = dialog.getWindow();
        window.setLayout(WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.WRAP_CONTENT);
        window.setGravity(Gravity.BOTTOM);
        window.setWindowAnimations(R.style.AnimBottom);
        dialog.show();
        Handler handler = new Handler();
        handler.postDelayed(() -> {
            String result = "";
            try {
                result = CommunicationModeSelector.futureTask.get(50, TimeUnit.SECONDS).toString();
                imageViewSigning.setImageResource(R.drawable.chenggong);
            } catch (ExecutionException | InterruptedException e) {
                e.printStackTrace();
                Toast.makeText(this, "获取签名异常", Toast.LENGTH_SHORT).show();
                dialog.dismiss();
                showPopupSignFailed();
                return;
            } catch (TimeoutException e) {
                e.printStackTrace();
                Toast.makeText(this, "签名超时", Toast.LENGTH_SHORT).show();
                dialog.dismiss();
                showPopupSignTimeout();
                return;
            }
            Handler handler1 = new Handler();
            String finalResult = result;
            handler1.postDelayed(() -> {
                if (!TextUtils.isEmpty(finalResult)) {
                    Intent intent1 = new Intent(this, TransactionDetailsActivity.class);
                    intent1.putExtra(TouchHardwareActivity.FROM, TAG);
                    intent1.putExtra("signed_raw_tx", finalResult);
                    startActivity(intent1);
                    dialog.dismiss();
                }
            }, 1000);

        }, 500);
    }

    private void showPopupSignFailed() {
        view = LayoutInflater.from(this).inflate(R.layout.signature_fail_popupwindow, null);
        imageViewCancel = view.findViewById(R.id.cancel_sign_fail);
        dialog = new Dialog(this, R.style.dialog);
        dialog.setContentView(view);
        Window window = dialog.getWindow();
        window.setLayout(WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.WRAP_CONTENT);
        window.setGravity(Gravity.BOTTOM);
        window.setWindowAnimations(R.style.AnimBottom);
        dialog.show();
        imageViewCancel.setOnClickListener(this);
    }

    private void showPopupSignTimeout() {
        view = LayoutInflater.from(this).inflate(R.layout.signature_timeout_popupwindow, null);
        Button button = view.findViewById(R.id.sign_again);
        imageViewCancel = view.findViewById(R.id.cancel_sign_timeout);
        dialog = new Dialog(this, R.style.dialog);
        dialog.setContentView(view);
        Window window = dialog.getWindow();
        window.setLayout(WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.WRAP_CONTENT);
        window.setGravity(Gravity.BOTTOM);
        window.setWindowAnimations(R.style.AnimBottom);
        dialog.show();
        imageViewCancel.setOnClickListener(this);
        button.setOnClickListener(this);
    }

    @Override
    public void onClick(View v) {
        switch (v.getId()) {
            case R.id.sign_again:
                dialog.dismiss();
                finish();
                break;
            case R.id.confirm_on_hardware:
                showPopupSignProcessing();
                break;
            case R.id.img_back:
                finish();
                break;
            default:
                dialog.dismiss();
        }
    }

    @Override
    protected void onRestart() {
        super.onRestart();
        finish();

    }
}
