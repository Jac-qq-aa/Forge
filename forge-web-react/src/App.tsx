import { useAppStore } from './stores';
import { Header } from './components/Header';
import { ConfigSection } from './components/ConfigSection';
import { ArticlesList } from './components/ArticlesList';
import { ModeSelection } from './components/ModeSelection';
import { ProcessingSection } from './components/ProcessingSection';
import { ResultSection } from './components/ResultSection';
import { ProfileInput } from './components/DeepMode';
import { Notification } from './components/common';

function App() {
  const { currentStep } = useAppStore();

  const renderStep = () => {
    switch (currentStep) {
      case 'config':
        return <ConfigSection />;
      case 'articles':
        return <ArticlesList />;
      case 'mode':
        return <ModeSelection />;
      case 'processing':
        return <ProcessingSection />;
      case 'result':
        return <ResultSection />;
      case 'deep':
        // Deep mode has multiple sub-steps
        // For now, render ProfileInput
        return <ProfileInput />;
      default:
        return <ConfigSection />;
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-4xl mx-auto px-4 py-8">
        <Header />
        {renderStep()}
      </div>
      <Notification />
    </div>
  );
}

export default App;