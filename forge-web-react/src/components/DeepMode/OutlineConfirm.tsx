import { useState } from 'react';
import { useAppStore, useDeepModeStore } from '../../stores';
import { Button, Card, TextArea } from '../common';
import { deepModeApi } from '../../services';

export function OutlineConfirm() {
  const {
    sessionId,
    outline,
    outlineVersion,
    outlineModified,
    setOutline,
    setOutlineModified,
    setContent,
  } = useDeepModeStore();
  const { setLoading, showNotification, setStep } = useAppStore();

  const [showModifyInput, setShowModifyInput] = useState(false);
  const [modification, setModification] = useState('');

  const handleAccept = async () => {
    if (!sessionId) return;

    setLoading(true, '正在生成全文...');

    try {
      // If outline was edited manually, update it first
      if (outlineModified) {
        await deepModeApi.updateOutline({
          session_id: sessionId,
          outline: outline,
        });
      }

      const result = await deepModeApi.outlineAction({
        session_id: sessionId,
        action: 'accept',
      });

      setContent(result.draft || '');
      showNotification('全文已生成！', 'success');
      // Move to content preview step
    } catch (error: any) {
      showNotification(`生成失败：${error.message}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleModify = async () => {
    if (!sessionId || !modification.trim()) {
      showNotification('请填写修改意见', 'error');
      return;
    }

    setLoading(true, '正在修改大纲...');

    try {
      const result = await deepModeApi.outlineAction({
        session_id: sessionId,
        action: 'modify',
        modification: modification,
      });

      setOutline(result.outline || '', result.outline_version);
      setShowModifyInput(false);
      setModification('');
      showNotification(`大纲已更新（版本 ${result.outline_version}）`, 'success');
    } catch (error: any) {
      showNotification(`修改失败：${error.message}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleBack = () => {
    setStep('deep');
  };

  const handleOutlineChange = (value: string) => {
    setOutline(value);
    setOutlineModified(true);
  };

  return (
    <Card badge="步骤 2.7" title="确认大纲">
      <p className="hint text-green-600 mb-4">大纲已生成，请确认或修改</p>

      <div className="bg-gray-100 p-4 rounded-lg mb-4">
        <h4 className="font-medium text-gray-700 mb-2">生成的大纲（可直接编辑）：</h4>
        <TextArea
          id="deep-outline-content"
          value={outline}
          onChange={handleOutlineChange}
          rows={12}
        />
      </div>

      {showModifyInput && (
        <TextArea
          id="deep-modify-input"
          label="修改意见"
          value={modification}
          onChange={setModification}
          placeholder="例如：把第二部分改成案例分析..."
          rows={2}
        />
      )}

      <div className="flex flex-wrap gap-3 mt-4">
        <Button onClick={handleAccept}>
          ✅ 确认大纲，生成全文
        </Button>

        {outlineModified && (
          <Button variant="warning" onClick={handleAccept}>
            ✅ 使用编辑后的大纲
          </Button>
        )}

        {!showModifyInput && (
          <Button onClick={() => setShowModifyInput(true)} variant="secondary">
            📝 AI 修改大纲
          </Button>
        )}

        {showModifyInput && (
          <Button onClick={handleModify}>
            提交修改
          </Button>
        )}

        <Button onClick={handleBack} variant="secondary">
          ← 返回修改需求
        </Button>

        <p className="hint text-xs mt-2">大纲版本：{outlineVersion}（最多 3 次）</p>
      </div>
    </Card>
  );
}