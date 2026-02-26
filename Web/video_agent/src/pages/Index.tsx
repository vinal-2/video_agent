import DashboardHeader from "@/components/DashboardHeader";
import PipelineSidebar from "@/components/PipelineSidebar";
import MainContent from "@/components/MainContent";
import { usePipeline } from "@/hooks/usePipeline";

const Index = () => {
  const pipeline = usePipeline();

  const sharedError = pipeline.error ?? pipeline.status?.error_detail ?? pipeline.status?.last_error ?? null;

  return (
    <div className="flex flex-col h-screen bg-background">
      <DashboardHeader
        status={pipeline.status}
        clipCount={pipeline.clipCount}
        logCount={pipeline.logs.length}
        warnings={pipeline.warnings}
        errorDetail={sharedError}
      />
      <div className="flex flex-1 overflow-hidden">
        <PipelineSidebar
          templates={pipeline.templates}
          config={pipeline.config}
          onConfigChange={pipeline.updateConfig}
          onRun={pipeline.runPipeline}
          onCancel={pipeline.cancelPipeline}
          running={pipeline.isRunning}
          phase={pipeline.phase}
          error={sharedError}
        />
        <MainContent
          status={pipeline.status}
          logs={pipeline.logs}
          segments={pipeline.segments}
          segmentStates={pipeline.segmentStates}
          trimData={pipeline.trimData}
          gradeData={pipeline.gradeData}
          transitionData={pipeline.transitionData}
          cropData={pipeline.cropData}
          samData={pipeline.samData}
          setSegmentState={pipeline.setSegmentState}
          updateTrim={pipeline.updateTrim}
          updateGrade={pipeline.updateGrade}
          updateTransition={pipeline.updateTransition}
          updateCrop={pipeline.updateCrop}
          updateSamMask={pipeline.updateSamMask}
          acceptAll={pipeline.acceptAll}
          rejectAll={pipeline.rejectAll}
          onRender={pipeline.renderAccepted}
          onReset={pipeline.resetAll}
          running={pipeline.isRunning}
          outputInfo={pipeline.outputInfo}
          clearLogs={pipeline.clearLogs}
          lastError={sharedError}
          segmentCounts={pipeline.segmentCounts}
          ffmpegProgress={pipeline.ffmpegProgress}
        />
      </div>
    </div>
  );
};

export default Index;
