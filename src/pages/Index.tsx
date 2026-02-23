import DashboardHeader from "@/components/DashboardHeader";
import PipelineSidebar from "@/components/PipelineSidebar";
import MainContent from "@/components/MainContent";

const Index = () => {
  return (
    <div className="flex flex-col h-screen bg-background">
      <DashboardHeader />
      <div className="flex flex-1 overflow-hidden">
        <PipelineSidebar />
        <MainContent />
      </div>
    </div>
  );
};

export default Index;
